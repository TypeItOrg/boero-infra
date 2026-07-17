# Boero

Infraestructura de despliegue compartida para Boero. Este repositorio es la fuente de verdad para Compose, Nginx, configuración de ambientes y operación en la VPS.

`boero-ui` y `boero-api` siguen construyendo, validando y publicando sus propias imágenes. Infra permite actualizar cada servicio de forma independiente.

## Staging

La topología actual usa una VPS con Nginx instalado en el host:

```text
Internet :80
  -> Nginx
     -> /api/v1  -> 127.0.0.1:8080 -> api
     -> /*       -> 127.0.0.1:3000 -> ui
  -> PostgreSQL y Redis sólo en la red de Compose
```

Preparar el ambiente:

```bash
cp .env.staging.example .env.staging
chmod 600 .env.staging
make bootstrap ENV=staging
```

Las imágenes deben usar etiquetas inmutables `sha-<commit>`. Sus versiones son independientes:

```bash
make deploy-ui ENV=staging VERSION=sha-<commit-ui>
make deploy-api ENV=staging VERSION=sha-<commit-api>
```

Cada despliegue actualiza únicamente el servicio indicado, espera su healthcheck y restaura la versión anterior si falla. Un lock por ambiente evita que dos pipelines modifiquen el archivo de versiones al mismo tiempo.

Operación habitual:

```bash
make status ENV=staging
make logs ENV=staging
make rollback-ui ENV=staging
make rollback-api ENV=staging
```

## Instalación en la VPS

```bash
sudo mkdir -p /opt/boero-infra
sudo chown "$USER":"$USER" /opt/boero-infra
git clone git@github.com:TypeItOrg/boero-infra.git /opt/boero-infra
cd /opt/boero-infra
cp .env.staging.example .env.staging
chmod 600 .env.staging
```

Completar los secretos y las versiones iniciales en `.env.staging`. Si GHCR es privado, autenticar Docker una vez con permiso `read:packages`.

### Migración desde los Compose de las aplicaciones

La primera adopción requiere un corte breve. Antes de actualizar los repositorios de aplicación y retirar sus archivos antiguos:

```bash
cd /opt/boero-ui
docker compose --env-file .env.staging -f compose.staging.yaml down

cd /opt/boero-api
docker compose --env-file .env.staging -f compose.staging.yaml down

cd /opt/boero-infra
make bootstrap ENV=staging
```

No agregar `--volumes`: el stack nuevo reutiliza los volúmenes existentes `boero-api-postgres-data-staging`, `boero-api-redis-data-staging` y `boero-ui-next-cache-staging`.
Compose los declara externos para conservarlos independientemente del ciclo de vida del stack. `make bootstrap` los crea de forma idempotente cuando todavía no existen.

Instalar el sitio Nginx:

```bash
sudo cp deploy/nginx/boero.conf.example /etc/nginx/sites-available/boero
sudo ln -s /etc/nginx/sites-available/boero /etc/nginx/sites-enabled/boero
sudo unlink /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl reload nginx
```

El entorno actual usa HTTP por IP. No debe contener datos reales y los puertos `3000` y `8080` deben permanecer limitados a loopback.

## GitHub Actions

Los repositorios de aplicación despliegan después de publicar correctamente su imagen de staging. El GitHub Environment `staging` necesita:

- `DEPLOY_HOST`: IP o hostname de la VPS.
- `DEPLOY_USER`: usuario operativo con acceso a Docker y `/opt/boero-infra`.
- `DEPLOY_SSH_KEY`: clave privada dedicada.
- `DEPLOY_SSH_KNOWN_HOSTS`: entrada verificada de `known_hosts` para la VPS.

La clave debe estar restringida al usuario operativo y no debe ser la clave personal de un integrante.
La entrada de `known_hosts` puede obtenerse con `ssh-keyscan <ip>`, pero su fingerprint debe verificarse contra la clave del servidor antes de guardarla como secret.

## Producción

`compose.production.yaml` y `.env.production.example` dejan preparada la misma interfaz operativa, pero ningún workflow despliega producción actualmente. Cuando exista el ambiente, debe configurarse mediante un GitHub Environment protegido y aprobación manual.

Los puertos por defecto coinciden con staging porque se asume otro host. Si ambos ambientes comparten una VPS, producción necesita puertos loopback y un punto de entrada Nginx diferentes.
