# QA aislado: configuración y operación

QA es un tercer ambiente (`ENV=qa`), no una rama. Se conservan `develop`, `staging`
y `main`. Esta entrega implementa y verifica sólo en recursos locales descartables;
no provisiona servidores, no publica DNS/certificados ni despliega remotamente.
Antes de activar en el mismo host que staging, verificar capacidad, puertos libres,
configuración efectiva, revisión de infra y salud. No reutilizar datos ni secretos de staging.

## Topología y recursos

`compose.yaml` + `compose.qa.yaml` forman el proyecto `boero-qa`, con contenedores
`boero-{ui,api,postgres,redis,mailpit}-qa` y red `boero-network-qa` propios.
PostgreSQL 18, Redis, logs, caché Next, archivos y correo tienen volúmenes independientes:

| Recurso | Nombre QA |
| --- | --- |
| PostgreSQL | `boero-api-postgres-data-qa` |
| Redis | `boero-api-redis-data-qa` |
| Logs API | `boero-api-logs-qa` |
| Archivos API | `boero-api-enrollment-storage-qa` |
| Caché UI | `boero-ui-next-cache-qa` |
| Mailpit | `boero-mailpit-data-qa` |

Los cinco volúmenes de aplicación son externos y `prepare` los crea sin borrar
contenidos. Mailpit tiene un volumen gestionado por Compose. Los nombres históricos
`*-staging` y `*-prod` no cambian. No conectar QA al PostgreSQL o Redis de staging.

Los puertos predeterminados son UI `127.0.0.1:3001`, API `127.0.0.1:8081` y Mailpit
`127.0.0.1:8026`; PostgreSQL/Redis y SMTP `mailpit:1025` son internos. La API usa
`SPRING_PROFILES_ACTIVE=qa`: PostgreSQL/Flyway, `ddl-auto=validate`, readiness de DB/Redis,
sin H2, `create-drop` ni Swagger. Sólo se ejecutan migraciones `db/migration` por
defecto; los datos demo se cargan explícitamente en recursos descartables, nunca
importando una base real. QA no deshabilita la protección de origen público de WebAuthn.

Mailpit captura el correo sin relay/outbound configurado, autenticación ni TLS SMTP.
El overlay fuerza `mailpit:1025` aunque se suministren variables SMTP externas por
error. Su UI permanece privada por loopback; usar un túnel SSH para consultarla en
un host remoto. Los valores `qa-unused` son sintéticos, no credenciales SMTP reales,
y satisfacen la interpolación heredada del Compose base.

Se necesita Docker Compose >= 2.24.4 por `!override`; no sustituirlo por listas de
puertos combinadas que podrían publicar también los puertos de staging.

## Configuración independiente

Una futura activación autorizada comienza en el checkout de infra del host correcto:

```bash
cp .env.qa.example .env.qa
chmod 600 .env.qa
```

Reemplazar los placeholders de SHA completos (40 caracteres hexadecimales minúsculos),
DB, JWT, clave de replay (32 bytes en base64) y administrador por valores **propios de QA**.
No copiar `.env.staging`/`.env.production`; `.env.qa` se ignora en Git. Dejar almacenamiento
local o configurar un bucket/prefijo/credenciales exclusivamente de QA. El correo
capturado y los backups también contienen datos sensibles: no copiar datos personales
reales ni exponer Mailpit públicamente.

| Variable | Valor público eventual QA |
| --- | --- |
| `FRONTEND_PUBLIC_URL` | `https://testing.typeit.com.ar` |
| `INSTITUTIONAL_BASE_DOMAIN` | `testing.typeit.com.ar` |
| `WEBAUTHN_RP_ID` | `testing.typeit.com.ar` (sin protocolo/ruta) |
| `WEBAUTHN_ALLOWED_ORIGINS` | `https://testing.typeit.com.ar` (sólo origen general) |
| `EMAIL_VERIFICATION_FRONTEND_URL` | `https://testing.typeit.com.ar` |
| `PASSWORD_RECOVERY_FRONTEND_URL` | `https://testing.typeit.com.ar` |
| `AUTH_COOKIE_SECURE` | `true` con HTTPS |

El acceso institucional previsto es `https://cboero.testing.typeit.com.ar` y necesita
`publicSubdomain=cboero` en la institución correcta. No renombra su slug ni se infiere
por una semilla. No hace falta agregar accesos institucionales a la lista global:
la API agrega únicamente el origen institucional activo resuelto para la solicitud.
No allowlistear instituciones hermanas ni usar comodines. Para la aceptación local
equivalente a QA con HTTPS, usar un proxy y certificado locales/temporales, DNS del
navegador y orígenes/RP ID consistentes; no activar `dev`/`test` para eludir el guard.

El desarrollo cotidiano pertenece a los repositorios API/UI y conserva
`http://localhost:3000` y `http://cboero.localhost:3000`. Chromium admite HTTP para
`.localhost`, pero considera `localhost` un dominio de primer nivel. En `dev`/`test`,
con RP ID configurado `localhost`, la API utiliza el hostname institucional validado
como RP ID de esa solicitud: `localhost` y `cboero.localhost` tienen passkeys distintas.
Las plantillas locales y `make dev` documentan y cargan esta configuración; no se
modifica el stack QA para resolverlo.

Los ambientes públicos conservan su RP ID común: `testing.typeit.com.ar` en QA y
`staging.typeit.com.ar` en staging. Las llaves no se migran entre RP IDs: se
registra una credencial por ambiente, aunque se use el mismo dispositivo. Producción
con `typeit.com.ar` se describe en [preparación de producción](PRODUCTION.md).

La UI y la API reciben ambas variables de acceso público. En ambientes antiguos
se permiten valores vacíos/ausentes para conservar el acceso genérico; el backend
conserva el fallback de URL de recuperación existente. Las nuevas variables están
en `.env.example` y `.env.qa.example`. No es necesario modificar un `.env` privado
existente para que el stack siga arrancando.

## Comandos

```bash
make preflight ENV=qa
make prepare ENV=qa
make bootstrap ENV=qa
make status ENV=qa
make logs ENV=qa
make logs-api ENV=qa
make logs-api-file ENV=qa
make logs-api-request ENV=qa REQUEST_ID=<id>
make deploy-ui ENV=qa VERSION=sha-<40-caracteres>
make deploy-api ENV=qa VERSION=sha-<40-caracteres>
make rollback-ui ENV=qa
make rollback-api ENV=qa
make backup-db ENV=qa
make down ENV=qa
```

`preflight` valida antes de mutar recursos; bootstrap vuelve a validarlo dentro del
lock del ambiente antes de crear volúmenes. Los deploys validan ambiente, servicio,
SHA y configuración candidata antes de crear volúmenes/cambiar `.env.qa`. La API
reutiliza `api-storage-init` con la imagen exacta antes de arrancar o volver al SHA
anterior; el auxiliar no tiene red, ni elimina archivos. Los despliegues son
inmutables, esperan salud y ante fallo vuelven a la versión anterior. Los errores
terminan con estado no cero; un rollback fallido requiere recuperación manual.
El lock es `/tmp/boero-infra-qa.lock`, el historial `.deploy/qa/<app>.previous`;
staging/production mantienen locks e historial separados. `down` no elimina volúmenes.

Readiness de API: `http://127.0.0.1:8081/actuator/health/readiness`; health UI:
`http://127.0.0.1:3001/api/health`. No exponer la API (`/api/v1`) ni Actuator mediante
el reverse proxy público; conservar Host y `X-Forwarded-Proto` hacia UI. El ejemplo
Nginx actual es de staging: no sobrescribirlo al activar QA, usar otro upstream/sitio
con puerto 3001 y certificado/dominio específicos. Nunca reutilizar su certificado
asumiendo cobertura wildcard.

Los backups son custom PostgreSQL validados con `pg_restore --list`, permisos `0600`,
retención después de la validación, y se guardan en `$BACKUP_DIR/qa` (por defecto
`/var/backups/boero/qa`). Comparten el lock QA. El timer existente es parametrizado;
`boero-backup@qa.timer` requiere instalación/activación separada autorizada. Probar
`pg_restore` únicamente contra una base **nueva, descartable y separada**; nunca contra
staging, production ni QA persistente para verificar un backup. Los dumps locales
no reemplazan una copia fuera del host. Rollback de imagen no deshace Flyway y sólo
es seguro con migraciones retrocompatibles; nunca editar migraciones aplicadas.

## CI e incorporación futura de GitHub Actions

En cada app, CI publica en GHCR para pushes a `develop`, `staging` y `main`, nunca
para PRs. API espera tanto `static-analysis` como `test` (fast/integración selectiva
en develop y suite completa en staging/main). UI espera formatting, lint, typecheck,
tests y build. Se publica `sha-<github.sha>` además del tag de rama. No crear rama
`testing`; una promoción de código sigue el flujo actual develop -> staging -> main.

`deploy-qa.yaml` sólo tiene `workflow_dispatch`; un push a develop no conecta por
SSH a QA. Acepta un SHA completo, verifica que sea un commit antecesor de
`origin/develop` y que exista la imagen `ghcr.io/typeitorg/<app>:sha-<SHA>` **antes**
de configurar SSH. El destino es fijo `environment: qa` / `ENV=qa`, sin input de
ambiente. Cada app tiene su propio workflow/versión y concurrencia; el lock remoto
coordina ambas. Staging conserva deploy automático por push; producción conserva
su workflow manual desde `main`. Esto describe los archivos locales, no prueba CI remoto.

Para una activación posterior autorizada:

1. El workflow debe existir en la **rama predeterminada de GitHub** para que
   `workflow_dispatch` aparezca en Actions. Llevarlo allí mediante el proceso habitual;
   no cambiar la rama predeterminada ni publicar este trabajo sin autorización.
2. Crear GitHub Environment `qa` en **ambos** repositorios, con revisores y restricciones
   de ramas seleccionadas que permitan el ref de dispatch elegido (p. ej. `develop`).
3. Configurar secrets propios `DEPLOY_HOST`, `DEPLOY_USER`, `DEPLOY_SSH_KEY`,
   `DEPLOY_SSH_KNOWN_HOSTS`, y opcionalmente `DEPLOY_PORT` (22 por defecto). Validar el
   fingerprint fuera de banda; no usar `ssh-keyscan` ciego ni desactivar host checking.
4. Habilitar lectura GHCR de las imágenes para Actions y para el usuario operativo
   remoto. Su autenticación Docker en el host no la configura el workflow.
5. Provisionar .env.qa/recursos propios y bootstrap después de backup/inventario de los
   ambientes existentes. El workflow remoto actualiza infra sólo con `git pull --ff-only`.
6. Publicar primero las imágenes por CI y seleccionar luego los SHAs API/UI compatibles
   en cada dispatch. Verificar estado, logs y URLs efectivas después.
7. Configurar DNS/HTTPS del dominio general e institucional de QA y del institucional
   de staging, y sólo entonces habilitar `publicSubdomain`/orígenes. Producción requiere
   otra activación. Ningún paso remoto se ejecuta en la aceptación local.

## Verificación local

`make test` corre pruebas de scripts con Docker simulado y renderiza Compose real
usando envs sintéticos temporales, no `.env` existentes. También ejecuta validación
de workflows con un grafo Git descartable y verifica que las políticas antiguas no
cambien. No afirma disponibilidad de un host ni éxito de Actions. La aceptación
completa de PostgreSQL/Flyway, backups/restauración y navegadores se coordina mediante
`make verify-qa-institutional-access`; sus evidencias están ignoradas bajo `build/verification/`.

Referencias upstream verificadas para la captura de correo: [healthchecks Mailpit](https://mailpit.axllent.org/docs/integration/healthcheck/)
y [versiones de Mailpit](https://github.com/axllent/mailpit/releases). El tag fijado
se puede actualizar con revisión y verificación local, no automáticamente.
