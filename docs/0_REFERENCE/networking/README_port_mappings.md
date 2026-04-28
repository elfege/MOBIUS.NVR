# Port Mappings — Global Allocation Scheme

All personal projects (`0_` prefix) and dDMSC use sequential port suites to avoid conflicts across machines.

| Project  | HTTP  | HTTPS | PgSQL | PostgREST | App  |
|----------|-------|-------|-------|-----------|------|
| dDMSC    | 8080  | 8443  | (ext) | 3000      | —    |
| **NVR**  | **8081** | **8444** | **5432** | **3001** | **5000** |
| SMART    | 8082  | 8445  | 5433  | 3002      | 5001 |
| JIRA     | 8083  | —     | 5434  | —         | —    |
| TILES    | 8084  | 8447  | 5435  | 3004      | 5002 |
| BACKUP   | 8085  | —     | —     | —         | —    |
| CLAUDE   | 8086  | —     | 5436  | —         | —    |

## This Project (NVR)

- **HTTP**: 8081 (host) → 80 (container nginx)
- **HTTPS**: 8444 (host) → 443 (container nginx)
- **PostgreSQL**: 5432 (host) → 5432 (container)
- **PostgREST**: 3001
- **App**: 5000
- Runs on: **dellserver**
- AWS secret: `NVR-Secrets` (profile 1)

## Notes

- `0_` prefix = personal project (ports can be remapped)
- No prefix = work project (ports fixed, different network)
- Canonical registry: `~/0_CLAUDE_IC/README_used_ports.md`
- AWS Secrets Manager values must match these assignments
