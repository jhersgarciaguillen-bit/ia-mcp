# Arquitectura inicial

## Componentes

```text
Hoy: desarrollo local

Navegador -> FastAPI en localhost -> /api/chat, /api/models/pull -> Ollama local
                                  -> lectura de Word / puente de Civil 3D
                                  -> edicion de Word con confirmacion explicita
MCP host -> servidor MCP stdio -> Ollama / Microsoft Word (COM) / puente de Civil 3D

Prototipo remoto (relay)

Navegador remoto -> HTTPS/WSS -> relay publico
                                      ^
                                      | WebSocket saliente del agente
                                      |
                        agente local en la PC
                           |              |
                     Ollama local    Word / Civil 3D local

Futuro: cuentas y varios dispositivos

Navegador -> web publicada -> cuenta y sesion autenticadas
                                      |
                           relay WSS con aislamiento por usuario
                                      |
                        uno o mas agentes emparejados
```

La web local y el servidor MCP `stdio` son procesos separados en esta base inicial. El host MCP
lanza el servidor y se comunica con el por stdin/stdout. La pagina local puede consultar
estados de loopback y enviar instrucciones a Ollama mediante `/api/chat`. Tambien puede leer el
texto de un documento de Word abierto y enviarlo como contexto; ese texto solo viaja a Ollama
en la misma PC. La pagina tampoco es aun un cliente MCP ni puede alcanzar un Ollama que corre en
la PC de otra persona.

La pagina puede descargar modelos con `/api/models/pull`, que reenvia el flujo NDJSON de Ollama
al navegador. La descarga ocurre en la PC local y no sube archivos. Para Word existen
`/api/word/append` y `/api/word/replace`: exigen `confirm: true`, aplican el cambio sobre el
documento abierto y nunca guardan el archivo. Las acciones de la interfaz muestran una
confirmacion antes de llamar a esos endpoints.

## Relay y agente local

El prototipo remoto usa dos procesos nuevos:

- `ia_mcp.relay.app`: sirve la UI en `/remote`, acepta agentes en `/ws/agent` y navegadores en
  `/ws/client`. Autentica con tokens separados (`IA_MCP_AGENT_TOKEN` y
  `IA_MCP_RELAY_CLIENT_TOKEN`), valida un saludo inicial, aplica una lista de acciones permitidas
  y enruta cada respuesta solo al cliente que hizo la solicitud. No monta la API HTTP local.
- `ia_mcp.agent.local_agent`: inicia la conexion saliente al relay, ejecuta el despacho local y
  responde por el mismo WebSocket. No abre puertos ni permite shell, rutas arbitrarias ni
  acciones fuera de la lista.

La UI detecta `/remote` o `?remote=1` y usa el WebSocket en lugar de `fetch` local. El token del
cliente solo se mantiene en memoria; la URL del relay y las preferencias no sensibles pueden ir
a `localStorage`. El agente conserva el token de agente fuera del navegador.

Las ediciones de Word desde el agente requieren tres condiciones: el operador local debe
configurar `IA_MCP_AGENT_ALLOW_EDITS=1`, la solicitud debe incluir `confirm: true` y el navegador
debe mostrar confirmacion. El agente registra la accion en el log de auditoria y no guarda el
archivo.

El relay actual es de un solo agente por par de tokens y no tiene cuotas ni aislamiento por
usuario. La etapa de cuentas debe agregar emparejamiento visible, autorizacion por dispositivo,
revocacion y limites de tasa. No se debe cambiar el servidor MCP `stdio` a un puerto publico.

## Limites del servidor MCP inicial

Las herramientas actuales son de solo lectura:

- `ollama_status`
- `ollama_list_models`
- `civil3d_status`
- `civil3d_list_alignments`
- `word_status`
- `word_list_documents`
- `word_read_document`

Word se consulta por COM sobre la instancia que ya esta abierta en la PC del usuario. Las
herramientas no abren archivos por ruta, no guardan cambios ni cierran la sesion. Cada llamada
COM se ejecuta fuera del bucle de eventos para no bloquear la web o el servidor MCP.

El servidor MCP excluye deliberadamente las operaciones que modifican un dibujo o documento.
La web local y el agente remoto solo permiten dos ediciones de Word (`append` y `replace`), con
confirmacion visible en el navegador y, en el agente, una habilitacion explicita del operador
local; ninguna guarda el archivo. Mas adelante la autorizacion por usuario debe registrar la
operacion y volver a validar el alcance y propietario antes de enviarla al agente. Civil 3D
sigue en solo lectura.

## Datos y privacidad

Esta primera pagina no registra cuentas ni guarda conversaciones en el servidor: el historial
vive en la memoria del navegador y las instrucciones/modelo se guardan en `localStorage`. Los
endpoints locales de chat y de Word solo aceptan clientes de loopback. En modo remoto, los
prompts y respuestas si pasan por el relay, que debe declarar su politica de privacidad y
ofrecer controles de retencion antes de usarse con datos reales. La inferencia y los archivos
siguen en la PC.

Los archivos de dibujos y documentos de Word no deben transferirse a la web por defecto. El
agente devuelve solo el texto solicitado como contexto y las herramientas de lectura deben
devolver los datos minimos que la conversacion necesita.
