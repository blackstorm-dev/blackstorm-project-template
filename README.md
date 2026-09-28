# Blackstorm project template · piloto

App HTTP mínima con CI, configuración por entorno y promociones con Kargo y ArgoCD.
El repo base es `blackstorm-dev/blackstorm-project-template`; los proyectos se crean desde este template.
Los manifiestos fuente viven en `deploy/base`, `deploy/staging` y `deploy/production`.

## Runner propio: primer hito

La plataforma eligió Actions con runners propios. `runner-check.yaml` permite comprobar manualmente
checkout y shell en el scale set `blackstorm-local`; requiere que infra haya conectado ARC a la
organización. [Procedimiento de plataforma](https://github.com/blackstorm-dev/blackstorm-infra/blob/main/docs/architecture/05-github-actions-runners.md).
El enlace estará disponible al publicar esos cambios de infra. El workflow de prueba también debe
estar publicado en `main` antes de poder ejecutarlo:

```bash
gh workflow run runner-check.yaml --ref main
gh run list --workflow runner-check.yaml --limit 5
gh run watch <run-id> --exit-status
```

Este chequeo no usa Docker Hub ni despliega la app. `ci.yaml` usa `blackstorm-local` y requiere
Docker habilitado en el runner por la plataforma.
`blackstorm-local` depende de la Mac de desarrollo y no es todavía un runner de producción.

## Requisitos

- Git y GitHub CLI (`gh`), autenticado con `gh auth login` y permisos para administrar el repo.
- Una cuenta Docker Hub, un namespace donde pueda publicar y un token con Read & Write.
- SOPS y age instalados (`brew install sops age` en macOS).
- Python 3.13 para tests locales. Docker para construir o probar imágenes localmente.

## Crear un proyecto desde el template

Sustituir `mi-app` por el nombre del proyecto. Usar entre 2 y 255 caracteres: minúsculas, números,
guiones o guiones bajos, para que también sea válido como nombre de repositorio en Docker Hub.

```bash
gh repo create blackstorm-dev/mi-app --private --template blackstorm-dev/blackstorm-project-template --clone
cd mi-app
```

Quien ejecute esto debe tener acceso al template y permiso para crear repos en la organización.
Los secrets y variables deben configurarse para cada repo nuevo con el procedimiento siguiente.
Para trabajar sobre el piloto existente, clonar `blackstorm-dev/blackstorm-project-template` en vez de crear
otro repo. El nombre `mi-app` es solo un ejemplo: el workflow usa el nombre real de cada repo.

Los archivos `*.example` contienen únicamente placeholders. Después de `make init`, usá
`make secrets FILE=<ruta-sin-.example>` para crear y completar cada secreto cifrado.
Para `deploy/*/secrets/` y `secrets/platform/`, agregá también la clave **pública** age del clúster
a los destinatarios de esas reglas en `.sops.yaml`, antes de cifrar. No compartas la clave privada.

## Configurar la publicación una vez por repo

Hacer esto en **el repo del proyecto nuevo**, por ejemplo `blackstorm-dev/mi-app`.
Los secrets no se copian al crear un repo desde el template. La imagen de ese proyecto será
`<namespace>/mi-app`; el nombre lo toma el workflow del repo de GitHub.

1. Abrir https://hub.docker.com e iniciar sesión. Ir a **Repositories → Create repository**.
   Elegir el namespace (usuario u organización de Docker Hub), poner el mismo nombre del repo de
   GitHub (`mi-app` en este ejemplo), elegir **Private** y pulsar **Create**.
   Si el repositorio ya existe, usarlo.
2. Abrir https://app.docker.com → avatar → **Account settings → Personal access tokens →
   Generate new token**. Darle una descripción, por ejemplo `github-mi-app`, elegir vencimiento y
   permisos **Read & Write**. Pulsar **Generate** y copiar el token: Docker no vuelve a mostrarlo.
   Guardarlo en un gestor de contraseñas. Si el equipo ya entregó un usuario, namespace y token
   con acceso de escritura al repositorio, usar esos datos y omitir la creación del token.
3. Desde el clon del proyecto, ejecutar:

   ```bash
   make init
   ```

   Genera `age.key` si falta, configura su pública en `.sops.yaml` y carga la privada como
   `SOPS_AGE_KEY` en GitHub usando tu sesión de `gh`. El destino se obtiene del remote `origin`.
   Al repetirlo conserva la clave. `age.key` está ignorada por Git y Docker: conservar una copia
   segura. Cada proyecto nuevo genera su propia clave; no usar la de infraestructura.
4. Desde la raíz del proyecto, abrir el archivo con SOPS:

   ```bash
   make secrets FILE=secrets/dockerhub.env
   ```

   Cargar estas dos entradas con los valores reales:

   ```dotenv
   DOCKERHUB_USERNAME=usuario-del-token
   DOCKERHUB_TOKEN=token-de-dockerhub
   ```

   Al guardar y cerrar, SOPS escribe `secrets/dockerhub.env` cifrado. Para elegir editor,
   agregar `EDITOR=nano` antes del comando. `FILE` es obligatorio; no hay archivo por defecto.
5. Configurar el namespace público de Docker Hub (usuario u organización, sin `/mi-app`):

   ```bash
   gh variable set DOCKERHUB_NAMESPACE --body '<namespace>'
   ```

   Usá tu namespace también en `deploy/platform.yaml` y `deploy/base/deployment.yaml`.
6. Revisar y commitear `.sops.yaml` y `secrets/dockerhub.env`. Nunca agregar `age.key`.
   En proyectos creados desde el template, reemplazar las credenciales heredadas por las propias
   antes de publicar; la clave nueva no descifra archivos cifrados para el proyecto original.

Actions recibe únicamente `SOPS_AGE_KEY` de GitHub. SOPS descifra `secrets/dockerhub.env` para
el comando de login; el token se enmascara en los logs y Docker cierra la sesión al finalizar.
La imagen se publica en `<DOCKERHUB_NAMESPACE>/<nombre-del-repo>:<commit-sha>`.

Para rotar el token, volver a abrir el archivo con el comando anterior, guardar y publicar el cambio cifrado.
Los antiguos secrets `DOCKERHUB_USERNAME` y `DOCKERHUB_TOKEN` de GitHub dejan de usarse con este
workflow; eliminarlos después de comprobar una publicación correcta con SOPS.

Esto configura los secretos de CI. La entrega de credenciales de lectura al cluster para descargar
imágenes privadas se configura por separado mediante el operador SOPS.

## Ejecutar y verificar la CI

En GitHub, abrir **Actions → CI → Run workflow**, elegir `main` y pulsar **Run workflow**.
Abrir la ejecución: `test` e `image` deben terminar en verde. Si el job queda **Queued** esperando
`blackstorm-local`, el runner de la plataforma debe estar disponible para ese repo y la Mac
que aloja el cluster local debe estar encendida.

También se puede ejecutar desde el checkout del proyecto:

```bash
gh workflow run ci.yaml --ref main
gh run list --workflow ci.yaml --limit 5
gh run watch <run-id> --exit-status
```

También corre con cada push a `main` o `master`, incluidos cambios en `deploy/`.
Los PR ejecutan tests y renderizan ambos entornos, sin acceso al registry.
El job `image` construye para Linux amd64 y arm64 cuando cambia el código. Si solo cambia
`deploy/` o el README, reutiliza la imagen de la última versión validada. Descarga por digest
y comprueba `/healthz` antes de publicar los tags que Kargo puede descubrir.
`deploy/` está fuera del contexto Docker y contiene exclusivamente configuración de despliegue.
El resumen de la ejecución muestra `<namespace>/<repo>@sha256:...`, la referencia que usará el deploy.
El tag del commit es una etiqueta de conveniencia; el digest identifica el contenido inmutable.

Antes de configurar SOPS y Docker Hub, el job `image` falla indicando el archivo, variable o clave que falta.
Después de configurarlos, usar el disparo manual anterior, sin crear un commit vacío.
Si falla, `gh run view <run-id> --log-failed` muestra el paso y la causa. Un error de login suele
requerir revisar el usuario, el token o su vencimiento; un push denegado requiere revisar el namespace,
el nombre del repositorio y los permisos de escritura.

## Probar localmente

```bash
python3 -m unittest discover -s tests -v
docker build -t blackstorm-example .
docker run --rm -p 127.0.0.1:8080:8080 blackstorm-example
```

En otra terminal: `curl --fail http://127.0.0.1:8080/healthz`. Se espera `ok`; Ctrl+C detiene el contenedor.
Para repetir la verificación con la imagen publicada, copiar la referencia por digest del resumen
de Actions y ejecutar:

```bash
docker login --username <usuario-dockerhub>
# En el prompt de password ingresar un PAT con lectura del repositorio.
docker run --rm --platform linux/amd64 -p 127.0.0.1:8080:8080 <namespace>/<repo>@sha256:<digest>
```

`--platform linux/amd64` permite probar esa misma imagen en una Mac con Apple Silicon.
## Promoción de imagen y configuración

CI publica un tag Git `ci-<run>-<attempt>-<image-sha>` únicamente después de las validaciones.
El tag apunta al commit fuente y su sufijo identifica la imagen validada. Kargo exige que ambos
coincidan antes de crear una versión promovible (Freight); un build pendiente no habilita el deploy.

Kargo renderiza el commit registrado en esa versión y guarda los manifiestos en las ramas
`deploy/staging` y `deploy/production`. Son ramas de salida administradas por Kargo; el desarrollo
sigue en `main` o `master`. Los secretos SOPS permanecen cifrados en esos manifiestos.

Staging se promueve automáticamente. Para producción, abrir **production → Promote** en Kargo
y elegir una versión verificada en staging. Para rollback, promover una versión anterior al mismo
entorno: se recuperan su imagen y sus manifiestos. Cada entorno usa sus propios secretos y valores.
Un rollback no recupera datos de la base ni reactiva credenciales revocadas.

La app lee `MESSAGE` desde el ConfigMap definido en `deploy/base/kustomization.yaml`; `/` muestra
ese mensaje y `/healthz` sigue respondiendo `ok`. Kustomize cambia el nombre del ConfigMap cuando
cambia su contenido, lo que actualiza el Deployment y provoca el rollout.
