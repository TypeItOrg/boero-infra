# QA y acceso institucional: contrato de aceptación

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

## Requisitos obligatorios

Cada caso debe tener evidencia vigente de ejecución. Implementado no significa
verificado. Los IDs se mantienen en el manifiesto de aceptación; una prueba omitida,
fallida, sin ejecutar o perteneciente a otro estado del código impide el cierre.

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
| V01 | El verificador rechaza casos faltantes, omitidos, fallidos y evidencia desactualizada. |

## Ejecución y cierre

Desde `boero-infra`: `make verify-qa-institutional-access`. Los repositorios hermanos
pueden indicarse con `API_REPO` y `UI_REPO`. El comando no lee secretos de ambientes
existentes ni reutiliza sus bases/volúmenes; sus recursos usan un namespace de aceptación.

La evidencia se escribe en `build/verification/qa-institutional-access/`, ignorada por
Git. Debe contener estado por caso/ID, comandos, resultados, capturas y huella de los
repositorios. Nunca incluir contraseñas, JWT, refresh tokens, cookies ni archivos privados.

Estados permitidos: **pendiente**, **implementado**, **verificado**, **bloqueado**.
Sólo se puede comunicar «implementación completa y verificada localmente; no
desplegada» cuando todos los casos obligatorios pasan y la auditoría final vincula
cada requisito con su código, prueba y evidencia. Un bloqueo no cuenta como éxito.

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

## Qué ejecuta el verificador

Requisitos locales: Docker en el contexto `default` con socket Unix local,
Compose compatible con `!override` (2.24.4 o posterior), OpenSSL, Python 3,
Java 21/Gradle del proyecto y pnpm/dependencias instaladas en la UI.

1. Ejecuta las pruebas negativas del propio verificador y las pruebas dirigidas
   de configuración/orquestación/workflows. Estas últimas simulan Docker/SSH;
   **no** prueban un despliegue remoto.
2. Reejecuta los filtros de API registrados en `scripts/verification/api-tests.json`
   con PostgreSQL/Flyway/Redis reales para las pruebas de integración y los
   controles de compilación/formato y `staticAnalysis` (NullAway/ECJ main/test,
   sin ejecutar más suites). Ejecuta las suites UI registradas en
   `ui-tests.json`, TypeScript y lint/formato sólo de los archivos cambiados.
3. Construye imágenes `prod` de la API/UI actuales y crea **dos stacks descartables**
   propios, con TLS autofirmado y resolución de hosts únicamente dentro de Chromium.
   Uno simula QA y otro el dominio de staging: ambos usan el perfil QA sin semillas
   implícitas. No son los ambientes compartidos existentes. Las identidades sintéticas
   de preparación se confirman sólo en estas bases; la prueba de registro confirma
   por separado un usuario nuevo mediante correo realmente capturado.
4. Ejecuta la batería Playwright sin mocks: flujos Chromium (correo, logos, permisos,
   refresh y llave virtual WebAuthn) y los dos rechazos directos HTTP de loginAttempt/JWT
   separados como `http-runtime`, no como evidencia visual. Después comprueba aislamiento, reinicio, Flyway y
   backup/restore en otra base **nueva**, nunca sobre una base existente.
5. Elimina sólo recursos identificados por namespace, propietario e IDs completos
   de esa ejecución. Compara HEAD/index, entorno privado, migraciones previas y
   recursos Docker preexistentes. Finalmente exige evidencia vigente para los 20 IDs.

Cada ejecución guarda sus reportes normalizados en `runs/<run-id>/`. Los mapas
registran nombres reales de pruebas: si una prueba se renombra, desaparece, se
omite o falla, el requisito queda incompleto. El manifiesto exige el tipo de prueba
necesario (unitaria, PostgreSQL, navegador o runtime), no permite reemplazar una
prueba real por un mock y verifica también la integridad de reportes y capturas
referenciados por el índice. `--audit-only` audita la evidencia existente sin ejecutar
pruebas; modificar cualquier fuente, borrar o alterar un reporte/captura invalida
ese cierre.

Las credenciales, correo completo, cookies y logs crudos permanecen en directorios
privados temporales, fuera de la evidencia. No se generan trazas de red. Si Docker
impide el cleanup, el comando falla y conserva el archivo de ownership privado
`/tmp/boero-acceptance-state-<run-id>.json`; se puede repetir el cleanup seguro con:

```sh
python3 scripts/verification/runtime.py stop --state /tmp/boero-acceptance-state-<run-id>.json
```

No usar `docker system prune`, `compose down -v` sobre proyectos compartidos ni
borrar recursos manualmente para hacer pasar el gate. Las imágenes locales de
aceptación quedan disponibles (no se eliminan imágenes ajenas). Opcionalmente
`ACCEPTANCE_API_IMAGE`/`ACCEPTANCE_UI_IMAGE` reutilizan una imagen local sólo si sus
etiquetas identifican la app y la huella exacta del árbol actual; una imagen antigua
se rechaza. El comando no publica imágenes ni se conecta por SSH.
