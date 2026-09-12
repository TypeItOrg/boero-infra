# Operación y hardening de staging

Staging está provisionado en un servidor autohosteado. Producción todavía no tiene infraestructura y no tuvo su primer despliegue. La configuración versionada describe el estado deseado; antes de operar hay que comprobar contenedores, variables efectivas, Nginx y salud en el host.

El cambio preparado en UI y API mantiene CI y publicación de imágenes, pero desactiva `deploy-staging` mediante `if: ${{ false }}`. La desactivación se hará efectiva cuando el cambio llegue a la rama cuyo workflow se ejecuta. Se conservan Compose, perfiles, ejemplos de variables y scripts.

## Arquitectura

```text
Internet por HTTPS
  -> Nginx del host :443
     -> /api/v1 -> 404 (la API no es pública)
     -> resto   -> 127.0.0.1:3000 -> ui
  -> red privada de Compose
     -> UI -> api:8080
     -> PostgreSQL
     -> Redis
```

`boero-infra` es dueño de Compose, Nginx, variables del ambiente y comandos operativos. Cada aplicación conserva su Dockerfile, desarrollo local, validaciones y publicación en GHCR.

UI y API continúan publicados en loopback para las comprobaciones operativas del host. PostgreSQL y Redis sólo están disponibles dentro de Compose.

## Preparación del checkout

```bash
git clone https://github.com/TypeItOrg/boero-infra.git /opt/boero-infra
cd /opt/boero-infra
cp .env.example .env.staging
chmod 600 .env.staging
```

Completar `.env.staging` sin versionarlo, usar la URL HTTPS pública del frontend en `PASSWORD_RECOVERY_FRONTEND_URL` y mantener `AUTH_COOKIE_SECURE=true`. `UI_VERSION` y `API_VERSION` deben usar imágenes inmutables `sha-<commit>`. Los backups usan por defecto `BACKUP_DIR=/var/backups/boero` y `BACKUP_RETENTION_DAYS=7`.

Para claves de acceso, configurar `WEBAUTHN_RP_ID` con el hostname público del frontend (sin protocolo ni ruta) y `WEBAUTHN_ALLOWED_ORIGINS` con su origen HTTPS exacto. En el staging actual: `WEBAUTHN_RP_ID=staging.typeit.com.ar` y `WEBAUTHN_ALLOWED_ORIGINS=https://staging.typeit.com.ar`. Compose exige ambas variables y las inyecta en la API.

Validar antes de iniciar:

```bash
make preflight ENV=staging
make bootstrap ENV=staging
make status ENV=staging
```

Los volúmenes son externos al proyecto Compose:

- `boero-ui-next-cache-staging`
- `boero-api-postgres-data-staging`
- `boero-api-redis-data-staging`
- `boero-api-logs-staging`

`make bootstrap` los crea si no existen. Nunca usar `down --volumes` como parte de una actualización normal.

## Migración realizada desde los repositorios de aplicación

Esta sección conserva el antecedente de la adopción inicial. No forma parte del bootstrap de una VPS nueva y no debe ejecutarse como si describiera el estado actual.

La adopción inicial detuvo los Compose independientes sin eliminar volúmenes y levantó el stack compartido:

```bash
cd /opt/boero-ui
docker compose --env-file .env.staging -f compose.staging.yaml down

cd /opt/boero-api
docker compose --env-file .env.staging -f compose.staging.yaml down

cd /opt/boero-infra
make bootstrap ENV=staging
```

Los Compose, env examples y configuraciones Nginx de staging/producción fueron retirados de `boero-ui` y `boero-api`.

## Acceso de GitHub Actions

Antes de reactivar despliegues, crear o revisar el GitHub Environment `staging` de `boero-ui` y `boero-api`, restringirlo a la rama `staging` y configurar estos secrets para la nueva VPS:

| Secret | Contenido |
|---|---|
| `DEPLOY_HOST` | IP o hostname de la VPS |
| `DEPLOY_USER` | Usuario operativo remoto |
| `DEPLOY_SSH_KEY` | Clave privada dedicada, sin passphrase |
| `DEPLOY_SSH_KNOWN_HOSTS` | Clave verificada del host SSH |

La clave pública correspondiente debe existir en `authorized_keys` del usuario remoto. El fingerprint de `DEPLOY_SSH_KNOWN_HOSTS` debe compararse con `/etc/ssh/ssh_host_ed25519_key.pub` en la VPS.

## Despliegue automático

Mientras `deploy-staging` tenga `if: ${{ false }}`, un push a `staging` valida y publica la imagen, pero omite toda conexión SSH.

Para reactivarlo, provisionar la VPS, completar la configuración y el bootstrap, verificar los secrets y restaurar en ambos workflows la condición `github.event_name == 'push' && github.ref_name == 'staging'`. Publicar ese cambio en la rama `staging` cuando se autorice la reactivación.

Una vez reactivado, un push a `staging`:

1. Ejecuta las validaciones de CI.
2. Publica `ghcr.io/typeitorg/<app>:sha-<commit>`.
3. Entra a la VPS por SSH.
4. Actualiza `boero-infra` mediante `git pull --ff-only`.
5. Ejecuta únicamente `make deploy-ui` o `make deploy-api`.
6. Espera el healthcheck y revierte al SHA anterior si falla.

Los locks `/tmp/boero-infra-git.lock` y `/tmp/boero-infra-staging.lock` evitan carreras entre pipelines.

## Operación cotidiana

Los siguientes comandos requieren un ambiente ya provisionado y operativo.

```bash
cd /opt/boero-infra

make status ENV=staging
make logs ENV=staging
make deploy-ui ENV=staging VERSION=sha-<commit>
make deploy-api ENV=staging VERSION=sha-<commit>
make rollback-ui ENV=staging
make rollback-api ENV=staging
```

Comprobaciones directas:

```bash
curl --fail http://127.0.0.1:3000/api/health
curl --fail http://127.0.0.1:8080/actuator/health/readiness
curl --fail https://<dominio>/
```

El rollback del API sólo es seguro cuando las migraciones de Flyway mantienen compatibilidad hacia atrás. Una migración aplicada nunca se revierte automáticamente.

## Nginx

La configuración fuente está en `deploy/nginx/boero.conf.example`. El ejemplo conserva `_` como valor genérico para `server_name` y para el directorio del certificado de Let's Encrypt; en el host debe mantenerse alineado con la configuración efectiva del certificado.

El sitio rechaza `/api/v1` y `/actuator` antes de llegar a las aplicaciones. Para preservar streaming, el proxy de UI mantiene el buffering desactivado.

En el host, conservar primero una copia recuperable de la configuración activa:

```bash
sudo cp /etc/nginx/sites-available/boero /etc/nginx/sites-available/boero.before-hardening
sudo cp deploy/nginx/boero.conf.example /etc/nginx/sites-available/boero
sudo nginx -t
sudo systemctl reload nginx
```

Los límites se instalan inicialmente con `limit_req_dry_run on` y `limit_conn_dry_run on`. Durante 48 horas representativas, revisar:

```bash
sudo grep -E 'limit_(req|conn)=(REJECTED_DRY_RUN|REJECTED)' /var/log/nginx/boero.access.log
```

Después de confirmar que una sesión normal con varios usuarios detrás de la misma IP no produce rechazos, cambiar ambas directivas a `off`, volver a ejecutar `sudo nginx -t` y recargar. En enforcement, los excesos reciben `429`. No asociar esos `429` con una cárcel de Fail2ban porque una IP puede representar una institución completa.

Validar desde fuera del host:

```bash
curl --fail https://<dominio>/api/health
curl --fail-with-body https://<dominio>/
test "$(curl --silent --output /dev/null --write-out '%{http_code}' https://<dominio>/api/v1)" = 404
test "$(curl --silent --output /dev/null --write-out '%{http_code}' https://<dominio>/api/v1/auth/login)" = 404
test "$(curl --silent --output /dev/null --write-out '%{http_code}' https://<dominio>/actuator)" = 404
```

El health debe responder correctamente; las tres rutas restringidas deben devolver `404`. La API continúa disponible únicamente para la UI en Docker y para readiness por loopback. Si se incorpora un CDN o proxy externo, configurar `real_ip` sólo con sus rangos oficiales antes de habilitar límites por IP.

## Backups locales

El backup diario usa `pg_dump` en formato custom, valida el archivo con `pg_restore --list`, aplica permisos `0600` y recién entonces elimina archivos que superan la retención. Comparte el lock de despliegue del ambiente para no competir con una actualización.

Preparar el directorio y ejecutar el primer backup antes de habilitar el timer:

```bash
sudo install -d -m 0700 /var/backups/boero/staging
sudo make -C /opt/boero-infra backup-db ENV=staging
sudo find /var/backups/boero/staging -type f -name '*.dump' -exec ls -lh {} \;
```

Estos archivos permiten recuperarse de errores operativos, pero no sobreviven a la pérdida del disco o del host. Copiarlos fuera de la máquina sigue siendo una tarea pendiente. Ensayar mensualmente una restauración en una base descartable; nunca restaurar sobre staging para probar el archivo.

## Timer de backup

Las unidades de `deploy/systemd` ejecutan el backup diariamente a las 02:15, con una demora aleatoria máxima de 15 minutos para evitar acoplarlo rígidamente a otras tareas del host. No ejecutan monitoreo ni limpieza automática de imágenes Docker.

Instalación:

```bash
sudo install -m 0644 deploy/systemd/boero-backup@.service deploy/systemd/boero-backup@.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now boero-backup@staging.timer
```

Comprobación inicial:

```bash
sudo systemctl start boero-backup@staging.service
sudo systemctl status boero-backup@staging.service
sudo systemctl list-timers boero-backup@staging.timer
sudo journalctl -u boero-backup@staging.service --since today
```
