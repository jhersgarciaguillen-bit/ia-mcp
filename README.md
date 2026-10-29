# IA_MCP

Proyecto nuevo e independiente para ofrecer una interfaz web de IA que use modelos
ejecutados en la computadora de cada persona y herramientas locales expuestas mediante MCP.
El proyecto anterior, `IA_civil.autocat`, se conserva sin modificaciones.

## Arquitectura objetivo

- **Web:** interfaz, cuentas y coordinacion; no aloja ni cobra por inferencia de modelos.
- **PC del usuario:** Ollama descarga y ejecuta los modelos que el usuario elija.
- **MCP local:** herramientas limitadas para consultar Ollama y programas instalados, empezando
  por Microsoft Word y Civil 3D.
- **Conector:** inicia una conexion saliente cifrada al servicio web para vincular la sesion
  del navegador con las herramientas de esa PC. No se publican puertos locales en Internet.

El navegador no debe conectarse directamente a un servidor MCP local ni abrir puertos en el PC.
La futura comunicacion web-PC se hara mediante un conector autenticado con conexion saliente.
MCP define el protocolo de herramientas; no sustituye esa conexion segura ni el control de
acceso entre usuarios.

## Estado actual

Esta primera base incluye:

- Una pagina web de chat servida por FastAPI, con seleccion y descarga de modelos de Ollama,
  instrucciones del asistente, selector de programa y contexto de Word o Civil 3D.
- Edicion de Word desde la web solo con confirmacion explicita: añadir texto al final o
  reemplazar el documento. La web nunca guarda el archivo por su cuenta.
- Un agente local que se conecta por WebSocket saliente a un relay y ejecuta solo acciones
  permitidas: estado, modelos, chat, Word y Civil 3D.
- Un relay WebSocket con tokens separados para agente y cliente, enrutamiento por solicitud y
  UI remota en `/remote`. El relay no expone la API HTTP local.
- Un servidor MCP local por `stdio`, compatible con hosts MCP.
- Herramientas de lectura para el estado y los modelos de Ollama.
- Herramientas de lectura para el estado de Civil 3D y sus alineamientos, sujetas a que haya
  un puente local compatible instalado.
- Herramientas de lectura para Microsoft Word: estado, documentos abiertos y texto de un
  documento abierto. No modifican, guardan ni cierran documentos.
- Pruebas unitarias con servicios HTTP y COM simulados, pruebas del agente y del relay con
  WebSockets, mas una prueba de integracion real con Word en Windows que tambien verifica
  añadir y reemplazar texto sin guardar.

**Aun no incluye** cuentas, emparejamiento multiple por usuario, aislamiento multi-inquilino,
un plugin de Civil 3D ni edicion autonoma de Word sin confirmacion. El relay es de un solo
agente por par de tokens: para varias personas hace falta un relay o capa de aislamiento por
usuario. Las herramientas de Word solo funcionan con Word abierto y no abren archivos por ruta.
Las herramientas MCP siguen siendo de solo lectura; la web y el agente solo editan Word tras
confirmar el usuario y si el operador local habilito las ediciones. No se declara que las
herramientas de Civil 3D funcionen hasta probarlas con el plugin en una instalacion real.

## Requisitos

- Windows 10/11 y Python 3.12 o posterior.
- Ollama local para consultar modelos.
- MCP host compatible para lanzar el servidor MCP por `stdio`.
- Microsoft Word en Windows solo para las herramientas de Word. La integracion usa COM a
  traves de `pywin32`, que se instala automaticamente en Windows.
- Civil 3D y el puente compatible solo para las herramientas de Civil 3D.

## Preparacion en Windows

Desde PowerShell, en la carpeta `IA_MCP`:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

Iniciar la pagina web:

```powershell
.\.venv\Scripts\uvicorn.exe ia_mcp.web.app:app --app-dir src --reload
```

Abrir `http://127.0.0.1:8000`.

## Usar la interfaz web

1. Asegurate de que Ollama este ejecutandose y de tener al menos un modelo
   (`ollama list`; si falta, `ollama pull qwen2.5-coder`).
2. Elige el modelo local en el panel derecho. Tambien puedes escribir el nombre de otro modelo
   de Ollama y pulsar **Descargar**; el progreso aparece en el panel.
3. Escribe las instrucciones del asistente (por ejemplo, idioma, tono y reglas). Se guardan solo
   en el navegador.
4. Elige el programa en **Programa**: solo chat, Word o Civil 3D.
5. Para Word: abre Word con un documento, pulsa **Actualizar**, elige el documento y pulsa
   **Cargar texto**. El texto se agrega como contexto de la conversacion y puedes editarlo antes
   de enviar tu instruccion. En las respuestas del asistente aparecen **Añadir al final de Word**
   y **Reemplazar documento**; ambas piden confirmacion y no guardan el archivo automaticamente.
6. Para Civil 3D: pulsa **Cargar alineamientos** (requiere el puente local). El resultado se usa
   como contexto de solo lectura.
7. Escribe la instruccion y pulsa **Enviar**. La inferencia ocurre en Ollama en esta PC.
8. Para acceso remoto, abre `/remote` en el relay, conecta con el token del cliente y usa la
   misma interfaz. En ese modo los prompts pasan por el relay antes de llegar al agente local.

En modo local, la conversacion se mantiene en la memoria de la pagina y no se envia a un
servidor remoto. En modo remoto, el relay transporta las solicitudes y respuestas, pero no
ejecuta inferencia ni recibe acceso directo a Word o Civil 3D. El historial no se guarda en
disco; las instrucciones, el modelo, el programa y la URL del relay se guardan en
`localStorage`, y el token del cliente solo vive en la memoria de la pagina. Los nombres de
modelo aceptados para descarga se limitan a letras, numeros y `._:/-`.

Ejecutar pruebas:

```powershell
.\.venv\Scripts\python.exe -m pytest
```

En Windows con Word instalado, la suite incluye una prueba de integracion que abre un Word
aislado, lee un documento temporal, añade y reemplaza texto, y lo cierra sin guardar. Se omite
automaticamente si Word no esta instalado o si ya hay una sesion de Word abierta, para no tocar
documentos del usuario.

## Probar el agente local y el relay

El relay y el agente permiten usar la pagina desde otra red sin abrir puertos en la PC. El
agente inicia la conexion WebSocket hacia el relay; el relay no puede iniciar conexiones hacia
la PC. Para una prueba local se necesitan dos secretos diferentes, de al menos 16 caracteres
(recomendado 32 o mas). Generalos en PowerShell sin compartirlos:

```powershell
.\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(32))"
```

Ejecuta ese comando dos veces: uno para el agente y otro para el cliente. En una terminal,
inicia el relay:

```powershell
$env:IA_MCP_AGENT_TOKEN="token-del-agente"
$env:IA_MCP_RELAY_CLIENT_TOKEN="token-del-cliente"
$env:IA_MCP_RELAY_HOST="127.0.0.1"
$env:IA_MCP_RELAY_PORT="8765"
.\.venv\Scripts\python.exe -m ia_mcp.relay.app
```

En otra terminal, inicia el agente con el mismo token del agente:

```powershell
$env:IA_MCP_AGENT_TOKEN="token-del-agente"
$env:IA_MCP_RELAY_URL="ws://127.0.0.1:8765/ws/agent"
# Opcional: habilita ediciones de Word solicitadas desde el navegador.
# $env:IA_MCP_AGENT_ALLOW_EDITS="1"
.\.venv\Scripts\python.exe -m ia_mcp.agent.local_agent
```

Abre `http://127.0.0.1:8765/remote`, escribe la URL del relay
(`ws://127.0.0.1:8765/ws/client`) y el token del cliente. El token del agente nunca se escribe
en el navegador. Si la pagina no muestra el agente como conectado, revisa el orden de arranque
y que ambos tokens coincidan con los configurados en el relay.

Para usarlo desde Internet, despliega el relay en un servicio con HTTPS/WSS y configura alli
`IA_MCP_AGENT_TOKEN` y `IA_MCP_RELAY_CLIENT_TOKEN`. En el agente cambia `IA_MCP_RELAY_URL` por
la URL `wss://.../ws/agent`. La inferencia y los archivos siguen en tu PC, pero los prompts y
respuestas pasan por el relay; revisa la privacidad del proveedor. Este proyecto no crea
cuentas ni despliega por ti.

## Desplegar el relay gratis en Render

El repositorio incluye `Dockerfile` y `render.yaml` para publicar solo el relay. La PC que
ejecuta Ollama y Word sigue siendo el agente; no se puede desplegar en la nube. Render ofrece
un plan gratuito con HTTPS/WSS, pero la instancia puede dormir por inactividad y no es adecuada
para produccion con datos sensibles.

El propietario debe crear la cuenta e iniciar sesion; no compartas contrasenas ni tokens con
terceros. Pasos:

1. Crea un repositorio en GitHub y sube el proyecto. No subas `.env` ni secretos:

   ```powershell
   git add .
   git commit -m "Relay y agente local"
   git branch -M main
   git remote add origin https://github.com/TU_USUARIO/IA_MCP.git
   git push -u origin main
   ```

2. En Render, elige **New > Blueprint**, conecta el repositorio y aplica `render.yaml`.
3. Render pedira dos variables. Genera dos secretos diferentes (32+ caracteres) y asigna:
   - `IA_MCP_AGENT_TOKEN`: lo usara el agente en tu PC.
   - `IA_MCP_RELAY_CLIENT_TOKEN`: lo usara el navegador.
4. Cuando termine el despliegue, abre `https://TU-SERVICIO.onrender.com/remote`, conecta con
   la URL `wss://TU-SERVICIO.onrender.com/ws/client` y el token del cliente.
5. En tu PC inicia el agente apuntando al relay publico:

   ```powershell
   $env:IA_MCP_AGENT_TOKEN="token-del-agente"
   $env:IA_MCP_RELAY_URL="wss://TU-SERVICIO.onrender.com/ws/agent"
   .\.venv\Scripts\python.exe -m ia_mcp.agent.local_agent
   ```

Si el servicio gratuito se durmio, la primera peticion tarda en despertar; el agente reintenta
la conexion automaticamente. Para produccion real usa un plan que no duerma, un proxy TLS con
WebSocket y las mejoras de cuentas/aislamiento pendientes.

## Configurar un host MCP

El servidor esta en `src/ia_mcp/mcp_servers/local_server.py` y usa transporte `stdio`.
Instala el proyecto en modo editable y configura el host MCP con el ejecutable Python del
entorno virtual y el modulo:

```json
{
  "mcpServers": {
    "ia-mcp-local": {
      "command": "C:\\\\PROYECTOS\\\\IA_MCP\\\\.venv\\\\Scripts\\\\python.exe",
      "args": ["-m", "ia_mcp.mcp_servers.local_server"],
      "env": {
        "OLLAMA_BASE_URL": "http://127.0.0.1:11434",
        "CIVIL3D_BASE_URL": "http://127.0.0.1:8756",
        "CIVIL3D_TOKEN": "configura-un-token-local"
      }
    }
  }
}
```

Sustituye la ruta por la ubicacion real del proyecto. El token es opcional si el puente local no
lo requiere; nunca publiques ni compartas ese valor.

No publiques el transporte `stdio` como un puerto de red.

## Herramientas MCP

- `ollama_status`, `ollama_list_models`
- `civil3d_status`, `civil3d_list_alignments`
- `word_status`: indica si Word esta instalado y si hay una sesion abierta.
- `word_list_documents`: lista los documentos abiertos en la sesion de Word.
- `word_read_document`: devuelve el texto de un documento abierto, por nombre o indice, con un
  limite configurable de caracteres.

Las herramientas de Word se conectan a la instancia de Word que ya esta abierta. Si Word no esta
abierto, devuelven un error claro; no inician Word ni abren archivos por ruta.

## Seguridad de herramientas

- Las herramientas del servidor MCP son de solo lectura en esta etapa.
- No se aceptan comandos de shell, codigo arbitrario, rutas de archivos ni nombres de
  herramientas fuera de la lista permitida.
- Las herramientas de Civil 3D requieren token local si el puente lo tiene configurado.
- Las herramientas de Word del MCP solo leen documentos ya abiertos; no abren rutas
  arbitrarias, no guardan cambios y no cierran la sesion del usuario.
- La web permite añadir o reemplazar texto en Word solo con `confirm: true` y confirmacion
  visible. Esas acciones no guardan el archivo y no pueden ejecutarse desde el MCP ni por el
  modelo sin aprobacion humana.
- Las operaciones que modifiquen dibujos se agregaran despues con confirmacion explicita en la
  interfaz y validacion en el servidor; el modelo no podra aprobarlas por si solo.
- La descarga de modelos solo acepta nombres con letras, numeros y `._:/-`, y se ejecuta contra
  el Ollama local; no se suben archivos a la web.
- El agente remoto solo ejecuta acciones de una lista permitida y el relay aplica la misma
  lista. No acepta shell, rutas arbitrarias ni nombres de herramientas libres.
- El relay usa tokens separados para agente y cliente. Usa secretos de 32+ caracteres,
  rotalos si se filtran y sirve siempre por `wss://` en produccion. Cualquier persona con el
  token del cliente puede usar las acciones habilitadas del agente.
- Las ediciones remotas de Word estan desactivadas salvo que el operador local configure
  `IA_MCP_AGENT_ALLOW_EDITS=1`; ademas se exige `confirm: true` y confirmacion en el navegador.
  El agente nunca guarda el archivo.
- El relay es de un solo agente por par de tokens y no tiene cuotas ni aislamiento por usuario.
  No lo uses con varias personas ni datos sensibles hasta agregar esa capa.
- El operador del relay puede ver y modificar el trafico que pasa por el (incluido el token del
  cliente si sirve la UI). Usa un relay propio o de confianza y HTTPS/WSS.
- Los modelos locales no tienen cuotas de inferencia del servidor, pero su velocidad depende
  del equipo, memoria y modelo. Se respetan las licencias y terminos de cada modelo.

## Despliegue y acceso remoto

La version actual necesita ejecutarse en la misma PC que Ollama y Word. GitHub Pages o Vercel
pueden alojar una pagina, pero no pueden ejecutar este backend FastAPI, Ollama ni COM de Word.
Un despliegue alli seria solo una landing o una interfaz sin funciones locales.

Para una URL publica gratuita y temporal desde esta PC, usa un tunel como Cloudflare Tunnel o
`localhost.run`. Recuerda que los endpoints validan loopback: si el tunel reenvia como
`127.0.0.1`, seguira funcionando, pero exponerlo permite que terceros usen tu Ollama y editen
Word. Protege el tunel y no compartas la URL.

La alternativa segura para la nube es el agente local con conexion WSS saliente descrito en
`docs/architecture.md`: la web publica nunca recibe acceso directo a la PC, y el agente solo
acepta operaciones permitidas y confirmadas. El prototipo ya esta implementado y se prueba con
el relay local; para produccion falta agregar cuentas, aislamiento por usuario y limites de
tasa. En un host publico, el relay debe escuchar detras de un proxy TLS que soporte WebSocket
(`Upgrade`) y usar `wss://`. Este proyecto no crea cuentas ni inicia sesion por ti: el
despliegue y las credenciales quedan a tu cargo.

## Siguientes etapas

1. Endurecer el agente y el relay: cuentas, emparejamiento visible, aislamiento por usuario,
   revocacion de tokens, limites de tasa y registro de auditoria persistente.
2. Implementar la web con cuentas y autorizacion de cada dispositivo; hoy el relay es de un
   solo agente por par de tokens.
3. Ampliar la edicion controlada de Word (formato, seleccion, deshacer) y registrar cambios;
   la version actual solo añade al final o reemplaza todo el texto con confirmacion.
4. Crear el servidor MCP de Civil 3D y probarlo en la otra PC con Autodesk instalado.
5. Desplegar el relay con HTTPS/WSS, persistencia, politica de privacidad y controles de
   produccion verificados; el despliegue lo realiza el propietario con su propia cuenta.

La conexion remota no estara habilitada hasta completar y probar la etapa del agente. No
introduzcas datos sensibles en un despliegue publico hasta definir su almacenamiento y
retencion.
