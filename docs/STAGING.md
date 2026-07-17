# Operación de staging

Este documento registra la instalación realizada en la VPS y el flujo operativo vigente.

## Arquitectura

```text
Internet por HTTP
  -> Nginx del host :80
     -> /api/v1 -> 127.0.0.1:8080 -> api
     -> resto   -> 127.0.0.1:3000 -> ui
  -> red privada de Compose
     -> PostgreSQL
     -> Redis
```

`boero-infra` es dueño de Compose, Nginx, variables del ambiente y comandos operativos. Cada aplicación conserva su Dockerfile, desarrollo local, validaciones y publicación en GHCR.

## Instalación inicial

En la VPS:

```bash
git clone https://github.com/TypeItOrg/boero-infra.git /opt/boero-infra
cd /opt/boero-infra
cp .env.staging.example .env.staging
chmod 600 .env.staging
```

Completar `.env.staging` sin versionarlo. `UI_VERSION` y `API_VERSION` deben usar imágenes inmutables `sha-<commit>`.

Validar antes de iniciar:

```bash
docker compose --env-file .env.staging -f compose.staging.yaml config --quiet
make bootstrap ENV=staging
make status ENV=staging
```

Los volúmenes son externos al proyecto Compose:

- `boero-ui-next-cache-staging`
- `boero-api-postgres-data-staging`
- `boero-api-redis-data-staging`

`make bootstrap` los crea si no existen. Nunca usar `down --volumes` como parte de una actualización normal.

## Migración realizada desde los repositorios de aplicación

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

Los repositorios `boero-ui` y `boero-api` tienen un GitHub Environment llamado `staging`, restringido a la rama `staging`, con estos secrets:

| Secret | Contenido |
|---|---|
| `DEPLOY_HOST` | IP o hostname de la VPS |
| `DEPLOY_USER` | Usuario operativo remoto |
| `DEPLOY_SSH_KEY` | Clave privada dedicada, sin passphrase |
| `DEPLOY_SSH_KNOWN_HOSTS` | Clave verificada del host SSH |

La clave pública correspondiente debe existir en `authorized_keys` del usuario remoto. El fingerprint de `DEPLOY_SSH_KNOWN_HOSTS` debe compararse con `/etc/ssh/ssh_host_ed25519_key.pub` en la VPS.

## Despliegue automático

Un push a `staging` en cualquiera de las aplicaciones:

1. Ejecuta las validaciones de CI.
2. Publica `ghcr.io/typeitorg/<app>:sha-<commit>`.
3. Entra a la VPS por SSH.
4. Actualiza `boero-infra` mediante `git pull --ff-only`.
5. Ejecuta únicamente `make deploy-ui` o `make deploy-api`.
6. Espera el healthcheck y revierte al SHA anterior si falla.

Los locks `/tmp/boero-infra-git.lock` y `/tmp/boero-infra-staging.lock` evitan carreras entre pipelines.

## Operación cotidiana

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
curl --fail http://<ip-staging>/
```

El rollback del API sólo es seguro cuando las migraciones de Flyway mantienen compatibilidad hacia atrás. Una migración aplicada nunca se revierte automáticamente.

## Nginx

La configuración fuente está en `deploy/nginx/boero.conf.example`. En el host:

```bash
sudo cp deploy/nginx/boero.conf.example /etc/nginx/sites-available/boero
sudo ln -s /etc/nginx/sites-available/boero /etc/nginx/sites-enabled/boero
sudo nginx -t
sudo systemctl reload nginx
```

Staging continúa por HTTP e IP. No debe usar datos reales; sólo los puertos administrativos necesarios y `80` deben estar expuestos públicamente.

