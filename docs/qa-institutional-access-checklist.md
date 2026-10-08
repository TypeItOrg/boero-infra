# QA y acceso institucional: checklist de verificación

Esta entrega está aprobada para implementación y verificación **local**. No autoriza
DNS público, certificados públicos, despliegues remotos, producción, commits ni pushes.

## Decisiones

- `ENV=qa`: UI, API, PostgreSQL, Redis, correo capturado, archivos y secretos propios.
  Dominio público: `testing.typeit.com.ar`, siguiendo la convención de `staging.typeit.com.ar`.
  El nombre interno `qa` se conserva; no se agrega una rama `testing`.
- Se conservan `develop`, `staging` y `main`. Publicación de `develop` tras CI;
  despliegue de QA manual por SHA. La política existente de staging no cambia.
- `publicSubdomain` opcional e independiente de `slug`, administrado por plataforma.
- Logos PNG/JPEG, máximo 2 MiB, administrados por plataforma o por una institución
  con `INSTITUTION_UPDATE`; almacenamiento y autorizaciones existentes se conservan.
- Accesos generales conservan selector. Los institucionales fijan contexto, nombre
  y logo: `cboero.testing.typeit.com.ar`, `cboero.staging.typeit.com.ar` y, en una
  activación futura, `cboero.typeit.com.ar`.

## Comportamientos a verificar

Esta checklist organiza las reglas de configuración y los comportamientos de acceso
institucional. La verificación automatizada usa las suites unitarias y de integración
de cada repositorio; los resultados deben corresponder al código actual.

| ID | Criterio |
| --- | --- |
| Q01 | QA no comparte servicios, redes, puertos, volúmenes, archivos ni secretos con otros ambientes. |
| Q02 | QA arranca con PostgreSQL/Flyway y healthchecks; no usa H2 ni create-drop. |
| Q03 | Preflight, bootstrap, deploy, rollback, backup, estado, logs y apagado soportan QA. |
| Q04 | Ambiente/SHA inválidos, preflight fallido y despliegue no saludable no alteran otros ambientes. |
| C01 | Develop publica sólo tras controles; PRs no publican; staging/main conservan sus flujos. |
| C02 | Deploy manual acepta SHA completo y apunta exclusivamente a QA. |
| I01 | Nombre público opcional, independiente, DNS-safe y reservado con unicidad PostgreSQL. |
| I02 | Resolver público mínimo; nombres desconocidos/inactivos son 404, indisponibilidad es 503. |
| A01 | Acceso general conserva selector y flujos; autenticación de plataforma se mantiene. |
| A02 | Acceso institucional sin selector, con identidad visible en escritorio/móvil y sin logo. |
| A03 | Formularios, JWT, loginAttempt y cabeceras no permiten cruzar instituciones. |
| A04 | Registro y login de llaves funcionan en accesos general/institucional y rechazan otros orígenes. |
| A05 | Refresh conserva rotación, deduplicación, propagación de cookies y fallos transitorios. |
| A06 | Registro, confirmación y recuperación usan tokens/URLs correctos, sin hosts hardcodeados. |
| L01 | Plataforma carga, reemplaza y elimina logos. |
| L02 | Institución hace lo mismo sólo con permiso y sobre su institución. |
| L03 | Validación de bytes, reemplazo seguro, actualización inmediata y fallback sin logo. |
| S01 | Se conservan migraciones anteriores, HEAD/index, trabajo ajeno, entorno privado y recursos existentes. |
| D01 | Ejemplos y runbook completos, separando activación pública de cierre local. |

## Ejecución de las suites

Desde cada repositorio:

```sh
# boero-infra
make test

# boero-api: unitarias e integración con PostgreSQL/Redis locales
./gradlew test

# boero-ui: unitarias y componentes/integración con Jest
pnpm test
```

Los controles de infraestructura usan datos sintéticos, Docker/SSH simulados y un
grafo Git temporal. Las integraciones de API usan sus bases locales descartables;
las de UI simulan la red y los límites del framework. Se conservan las comprobaciones
de permisos, contexto institucional, sesiones, tokens y persistencia.

## Activación pública posterior (fuera de esta entrega)

- Verificar capacidad, puertos, host y configuración efectiva antes de operar.
- Configurar secretos independientes, GitHub Environment `qa` y sus credenciales;
  el workflow manual debe existir en la rama predeterminada de GitHub.
- Configurar DNS/HTTPS de QA y del acceso institucional de staging.
- Aplicar la migración nueva por el procedimiento de despliegue con backup; no
  editar el historial Flyway ni recrear una base persistente.
- Configurar expresamente el nombre público `cboero` sobre la institución correcta;
  no inferirlo de una semilla ni renombrar su slug.
- Validar dominio, salud, correo, RP ID/orígenes y versión API/UI antes de habilitar acceso.
- Producción y su dominio institucional requieren una activación separada.
