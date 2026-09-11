<div align="center">

<br />
<img src="assets/logo.svg" alt="Boero" width="80" height="80" />

# Boero

**Infraestructura compartida, despliegue y operación de los ambientes de Boero.**

[![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?style=for-the-badge&logo=docker&logoColor=white)](https://www.docker.com/)
[![Nginx](https://img.shields.io/badge/Nginx-Reverse_Proxy-009639?style=for-the-badge&logo=nginx&logoColor=white)](https://nginx.org/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-18-4169E1?style=for-the-badge&logo=postgresql&logoColor=white)](https://www.postgresql.org/)
[![Redis](https://img.shields.io/badge/Redis-7-FF4438?style=for-the-badge&logo=redis&logoColor=white)](https://redis.io/)

[![GitHub Actions](https://img.shields.io/badge/CD-GitHub_Actions-2088FF?style=flat-square&logo=githubactions&logoColor=white)](https://github.com/features/actions)
[![GHCR](https://img.shields.io/badge/Registry-GHCR-181717?style=flat-square&logo=github&logoColor=white)](https://ghcr.io)
[![Shell](https://img.shields.io/badge/Shell-POSIX-4EAA25?style=flat-square&logo=gnubash&logoColor=white)](https://pubs.opengroup.org/onlinepubs/9799919799/)
[![Linux](https://img.shields.io/badge/Runtime-Linux-FCC624?style=flat-square&logo=linux&logoColor=black)](https://www.linux.org/)

_Despliega artefactos inmutables, separa responsabilidades y mantiene una operación reproducible por ambiente._

</div>

## Disponibilidad de los ambientes

El desarrollo local continúa en los repositorios de UI y API. **Staging está provisionado en un servidor autohosteado; producción todavía no dispone de infraestructura.** La configuración versionada no reemplaza la comprobación del estado real del host.

Los pipelines de las aplicaciones mantienen CI y publicación de imágenes. El cambio preparado para ambos repositorios desactiva el job automático `deploy-staging`; producción conserva su workflow manual. La desactivación tendrá efecto en GitHub cuando ese cambio llegue a la rama cuyo workflow se ejecuta.

- [Operación y hardening de staging](docs/STAGING.md).
- [Preparación de producción](docs/PRODUCTION.md).

Conservar Compose, perfiles, ejemplos de variables y scripts de ambos ambientes. Verificar contenedores, salud y configuración efectiva antes de operar staging; no ejecutar producción hasta provisionar su infraestructura.
