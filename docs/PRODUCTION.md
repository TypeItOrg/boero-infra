# Preparación de producción

Producción está preparada estructuralmente, pero permanece inactiva hasta el primer release. No debe ejecutarse `make bootstrap ENV=production` hasta completar este documento.

## Topología prevista

La configuración asume una VPS separada de staging:

- Nginx es el único punto de entrada público.
- UI escucha en `127.0.0.1:3000`.
- API escucha en `127.0.0.1:8080`.
- PostgreSQL y Redis sólo existen dentro de la red de Compose.
- Los volúmenes persistentes terminan en `-prod`.
- UI y API se despliegan independientemente por SHA.

Si producción comparte host con staging, se deben definir otros puertos y sitios Nginx antes del bootstrap. Los defaults actuales no permiten ejecutar ambos ambientes simultáneamente en la misma VPS.

## Preparación del servidor

Cuando exista la VPS:

```bash
git clone https://github.com/TypeItOrg/boero-infra.git /opt/boero-infra
cd /opt/boero-infra
cp .env.production.example .env.production
chmod 600 .env.production
```

Generar secretos nuevos; nunca copiar los de staging. Establecer versiones de UI/API publicadas desde `main` y validar:

```bash
docker compose --env-file .env.production -f compose.production.yaml config --quiet
```

El primer arranque será:

```bash
make bootstrap ENV=production
make status ENV=production
```

## GitHub Environment

Crear `production` en `boero-ui` y `boero-api` únicamente cuando exista el servidor:

- Restringirlo a `main`.
- Agregar al menos un required reviewer.
- Desactivar aprobación por el actor que inició el workflow si la organización lo permite.
- Configurar `DEPLOY_HOST`, `DEPLOY_USER`, `DEPLOY_SSH_KEY` y `DEPLOY_SSH_KNOWN_HOSTS`.
- Usar una clave y usuario diferentes de staging.

Los workflows `Deploy production` son manuales. Solicitan un SHA completo, comprueban que pertenece a `main` y luego esperan la aprobación del Environment antes de conectarse a la VPS.

## Checklist obligatorio antes del release

- Dominio definitivo y DNS configurado.
- HTTPS válido, redirección HTTP a HTTPS y `AUTH_COOKIE_SECURE=true`.
- Usuario de despliegue sin acceso root, autorizado sólo para Docker y `/opt/boero-infra`.
- Firewall con únicamente SSH restringido y puertos `80/443` públicos.
- Credenciales productivas aleatorias y almacenadas fuera del repositorio.
- Backup automático y cifrado de PostgreSQL hacia almacenamiento externo.
- Restauración completa ensayada sobre otro servidor.
- Monitoreo de disponibilidad, disco, memoria, CPU y expiración TLS.
- Alertas con responsables y canal de escalamiento definidos.
- Política de retención y rotación de logs revisada.
- Capacidad de disco suficiente para imágenes, base, backups y crecimiento.
- Prueba de carga representativa y límites de contenedores ajustados.
- Revisión de migraciones Flyway y compatibilidad con rollback.
- Release candidato validado previamente en staging con los mismos SHA.
- Procedimiento de incidentes y ventana de mantenimiento comunicados.

Backups, TLS y monitoreo no están implementados todavía. Son bloqueantes explícitos, no tareas opcionales posteriores al lanzamiento.

## Primer release

1. Promover y validar los commits candidatos en `main`.
2. Confirmar que CI publicó ambos `sha-<commit>`.
3. Registrar versiones actuales y comprobar el último backup restaurable.
4. Ejecutar manualmente `Deploy production` para API.
5. Validar readiness, migraciones y funciones críticas.
6. Ejecutar manualmente `Deploy production` para UI.
7. Ejecutar smoke tests externos y observar métricas/logs.
8. Registrar los SHA finales y el resultado del release.

Para rollback:

```bash
make rollback-ui ENV=production
make rollback-api ENV=production
```

El rollback de aplicación no deshace migraciones. Los cambios destructivos de base deben diseñarse en fases compatibles.

