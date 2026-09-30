"""
Chatbot "Dahian"
----------------
Un chatbot en Python (Flask) con una interfaz web que se abre en Chrome.

Cómo funciona el conocimiento de Dahian:
1. Primero revisa una base de conocimiento local (rápida, sin internet)
   para saludos, cálculos, fecha/hora, y temas predefinidos.
2. Si el mensaje es sobre la TIENDA (productos, precios, disponibilidad,
   categorías o intención de compra del Centro de Biotecnología
   Agropecuaria), consulta DIRECTAMENTE la base de datos relacional
   (tienda_db.py / tienda.db) y responde con datos reales, guiando
   incluso un pequeño flujo de pedido (ver tienda_asistente.py).
3. Si configuraste una GEMINI_API_KEY (gratis, ver LEEME.txt), le pregunta
   a Google Gemini: una IA real que entiende la pregunta y redacta su
   propia respuesta, como ChatGPT. También recuerda la conversación.
4. Si no hay API Key configurada (o Gemini falla), usa como respaldo una
   búsqueda de fragmentos en DuckDuckGo / Wikipedia (menos inteligente,
   pero funciona sin ninguna clave).

Instalación:
    pip install flask requests beautifulsoup4

Configuración (opcional pero muy recomendada):
    Reemplaza GEMINI_API_KEY al inicio de este archivo con tu propia clave
    gratuita de Google AI Studio (ver LEEME.txt para el paso a paso).

Ejecución:
    python app.py
    Luego abre Chrome en: http://127.0.0.1:5000
"""

import re
import math
import datetime
import unicodedata
import requests
from bs4 import BeautifulSoup
from functools import wraps
from flask import Flask, render_template, request, jsonify, session, redirect, url_for, Response

import tienda_db
import tienda_asistente

app = Flask(__name__)
# Necesaria para que Flask pueda usar "session" (memoria de la conversación
# por navegador). Puedes cambiar este texto por cualquier otro si quieres.
app.secret_key = "clave-secreta-dahian-cambia-esto-si-quieres"

# Crea (si no existe) y llena con datos de demostración la base de datos
# de la tienda del Centro de Biotecnología Agropecuaria.
tienda_db.inicializar_db()

NOMBRE_IA = "Dahian"

# ---------------------------------------------------------------------
# CONFIGURACIÓN DE GOOGLE GEMINI (IA real, gratis)
# ---------------------------------------------------------------------
# 1. Ve a https://aistudio.google.com/apikey
# 2. Inicia sesión con una cuenta de Google y crea una API Key (gratis).
# 3. Pega tu clave aquí abajo, entre las comillas.
GEMINI_API_KEY = "PON_AQUI_TU_API_KEY"

# Modelo de Gemini a usar. "gemini-3.5-flash" es rápido, gratuito dentro
# de límites generosos, y de buena calidad para conversación general.
# Si quieres respuestas más "inteligentes" (a costa de ser un poco más
# lento), puedes cambiarlo por "gemini-3.8-flash". Si Google cambia los
# nombres de modelos y este deja de funcionar, entra a
# https://aistudio.google.com/ y revisa qué modelos aparecen disponibles,
# luego reemplaza el valor aquí.
MODELO_GEMINI = "gemini-3.5-flash"

# Cuántos mensajes anteriores (usuario + Dahian) se le mandan a Gemini como
# contexto, para que la conversación tenga memoria real. Un número más
# grande da más memoria pero también más lento/costoso.
MAX_HISTORIAL = 20

# ---------------------------------------------------------------------
# 1. BASE DE CONOCIMIENTO LOCAL
# ---------------------------------------------------------------------

SALUDOS = ["hola", "buenas", "buenos dias", "buenos días", "hey", "qué tal", "que tal"]
DESPEDIDAS = ["adios", "adiós", "chao", "hasta luego", "nos vemos", "bye"]
AGRADECIMIENTOS = ["gracias", "muchas gracias", "te lo agradezco"]

RESPUESTAS_BASE = {
    "quien eres": f"Soy {NOMBRE_IA}, un chatbot en Python que combina respuestas propias con búsquedas en internet para intentar responder casi cualquier pregunta.",
    "quién eres": f"Soy {NOMBRE_IA}, un chatbot en Python que combina respuestas propias con búsquedas en internet para intentar responder casi cualquier pregunta.",
    "como te llamas": f"Me llamo {NOMBRE_IA}.",
    "cómo te llamas": f"Me llamo {NOMBRE_IA}.",
    "que puedes hacer": "Puedo saludarte, hacer cálculos matemáticos, decirte la fecha y hora, y responder preguntas generales buscando en internet.",
    "ayuda": "Pregúntame lo que quieras: cálculos ('cuánto es 24*7'), fecha y hora, o cualquier tema general ('háblame de la fotosíntesis').",
}


def _texto_es_solo(texto, lista_frases, max_palabras=4):
    """
    Considera que el texto ES un saludo/despedida/agradecimiento solo si
    el mensaje es corto y coincide casi por completo con una de las frases.
    Así "hola" activa el saludo, pero "hola, quiero saber sobre..." no.
    """
    limpio = texto.strip(" ?¿!¡.,")
    if len(limpio.split()) > max_palabras:
        return False
    return any(limpio == s or limpio.startswith(s) for s in lista_frases)


def es_saludo(texto):
    return _texto_es_solo(texto, SALUDOS)


def es_despedida(texto):
    return _texto_es_solo(texto, DESPEDIDAS)


def es_agradecimiento(texto):
    return _texto_es_solo(texto, AGRADECIMIENTOS)


def intentar_calculo(texto):
    """Detecta operaciones matemáticas simples tipo '2+2', 'cuanto es 5*3',
    'divide 10 entre 30', '5 mas 3', '9 menos 4', '6 por 7'."""
    limpio = texto.lower()
    limpio = limpio.replace("cuanto es", "").replace("cuánto es", "")
    limpio = limpio.replace("cuanto da", "").replace("cuánto da", "")
    limpio = limpio.replace("calcula", "").replace("resuelve", "")
    limpio = limpio.replace("divide", "").replace("multiplica", "")
    limpio = limpio.replace(" entre ", "/").replace(" dividido entre ", "/")
    limpio = limpio.replace(" dividido por ", "/").replace(" dividido ", "/")
    limpio = limpio.replace(" por ", "*").replace(" multiplicado por ", "*")
    limpio = limpio.replace(" mas ", "+").replace(" más ", "+")
    limpio = limpio.replace(" menos ", "-")
    # Quita signos de interrogación / puntuación sueltos al final o inicio
    # (sin esto, "2 por 2?" quedaba como "2*2?" y no calculaba nada).
    limpio = limpio.strip(" ?¿!¡.,")

    if re.fullmatch(r"[0-9\.\+\-\*\/\(\)\s\^%]+", limpio) and any(ch.isdigit() for ch in limpio):
        expresion = limpio.replace("^", "**")
        try:
            resultado = eval(expresion, {"__builtins__": {}}, {"math": math})
            return f"El resultado es: {resultado}"
        except Exception:
            return None
    return None


def responder_fecha_hora(texto):
    ahora = datetime.datetime.now()
    if "hora" in texto:
        return f"Ahora mismo son las {ahora.strftime('%H:%M:%S')}."
    if "fecha" in texto or "día es hoy" in texto or "dia es hoy" in texto:
        dias = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
        return f"Hoy es {dias[ahora.weekday()]}, {ahora.strftime('%d/%m/%Y')}."
    return None


FRASES_RELLENO = [
    "hola,", "hola", "buenas,", "buenas",
    "que es", "qué es", "quien es", "quién es", "hablame de",
    "háblame de", "dime sobre", "dime", "dame", "nombra", "menciona",
    "explica", "explícame", "que sabes de",
    "qué sabes de", "cuentame sobre", "cuéntame sobre",
    "quiero saber donde esta ubicado", "quiero saber dónde está ubicado",
    "quiero saber donde esta ubicada", "quiero saber sobre",
    "quiero saber", "donde esta ubicado", "dónde está ubicado",
    "donde queda", "dónde queda", "donde esta", "dónde está",
    "cuales son", "cuáles son", "cual es", "cuál es",
]


def _limpiar_tema(pregunta):
    tema = pregunta
    for frase in FRASES_RELLENO:
        tema = tema.replace(frase, "")
    return tema.strip(" ?¿!¡.,")


def _normalizar(texto):
    """Pasa a minúsculas y quita tildes, para comparar sin importar acentos."""
    forma = unicodedata.normalize("NFKD", texto.lower())
    return "".join(c for c in forma if not unicodedata.combining(c))


def _palabras_clave(texto, min_len=4):
    """Extrae palabras 'significativas' (de cierta longitud) de un texto,
    ignorando tildes, para comparar qué tan relacionados están dos textos."""
    normalizado = _normalizar(texto)
    palabras = re.findall(r"[a-z0-9]+", normalizado)
    return {p for p in palabras if len(p) >= min_len}


def _es_relevante(tema, texto_resultado):
    """
    Verifica que un resultado tenga algo que ver con lo que se preguntó,
    comparando palabras clave en común. Esto evita que, por ejemplo, una
    pregunta sobre 'marcas de carros' devuelva un resultado sobre un
    festival de música que no tiene ninguna relación.
    """
    claves_tema = _palabras_clave(tema)
    if not claves_tema:
        return True
    claves_resultado = _palabras_clave(texto_resultado)
    return bool(claves_tema & claves_resultado)


def _buscar_instant_answer(tema):
    """
    Intenta primero la Instant Answer API de DuckDuckGo (respuestas tipo
    'ficha resumen', similar a lo que da Wikipedia). Es gratis y no
    necesita API Key, pero no siempre tiene información de todos los temas.
    Devuelve None si no encontró nada útil aquí (para pasar al plan B).
    """
    resp = requests.get(
        "https://api.duckduckgo.com/",
        params={
            "q": tema,
            "format": "json",
            "no_html": 1,
            "skip_disambig": 1,
            "kl": "es-es",
        },
        headers={"User-Agent": f"{NOMBRE_IA}-chatbot/1.0"},
        timeout=8,
    )
    resp.raise_for_status()
    datos = resp.json()

    extracto = datos.get("AbstractText") or datos.get("Answer")

    # Ojo: no usamos "RelatedTopics" como respaldo aquí. Esa parte de la
    # API suele traer temas que solo comparten una palabra suelta con la
    # pregunta (ej. una pregunta sobre "marcas de carros" devolviendo un
    # festival de música), y terminaba dando respuestas incorrectas.
    if extracto and _es_relevante(tema, extracto):
        return extracto.strip()

    return None


def _buscar_html(tema):
    """
    Plan B: la versión HTML de DuckDuckGo (html.duckduckgo.com), que no
    requiere API Key y da resultados normales de búsqueda web, para
    cuando la Instant Answer API no tiene nada sobre el tema.

    Usa BeautifulSoup en lugar de expresiones regulares propias: el HTML
    de DuckDuckGo varía (a veces el fragmento de texto viene en una
    etiqueta <a>, otras en un <div> o <span>), y buscar por clase CSS con
    BeautifulSoup funciona sin importar qué etiqueta use.
    """
    resp = requests.get(
        "https://html.duckduckgo.com/html/",
        params={"q": tema, "kl": "es-es"},
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            )
        },
        timeout=10,
    )
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    bloques = soup.select("div.result, div.web-result")

    resultados = []
    for bloque in bloques:
        elem_titulo = bloque.select_one("a.result__a")
        elem_snippet = bloque.select_one(".result__snippet")

        if not elem_titulo:
            continue

        # separator=" " evita que palabras de distintas etiquetas queden
        # pegadas (ej. "Tolimaes" en vez de "Tolima es"), algo que pasaba
        # cuando DuckDuckGo resalta partes del texto con <b> intercalados.
        titulo = elem_titulo.get_text(separator=" ", strip=True)
        snippet = elem_snippet.get_text(separator=" ", strip=True) if elem_snippet else ""
        titulo = re.sub(r"\s+", " ", titulo)
        snippet = re.sub(r"\s+", " ", snippet)

        # Descartamos resultados que no comparten ninguna palabra clave
        # con la pregunta (para no mezclar temas sin relación).
        if titulo and _es_relevante(tema, f"{titulo} {snippet}"):
            resultados.append(f"• {titulo}: {snippet}" if snippet else f"• {titulo}")

        if len(resultados) >= 3:
            break

    if not resultados:
        return None

    partes = [f"Esto es lo que encontré sobre '{tema}':"]
    partes.extend(resultados)
    return "\n".join(partes)


def _buscar_wikipedia(tema):
    """
    Plan C: consulta directa a la API oficial de Wikipedia en español.
    Sirve especialmente para preguntas factuales tipo "capital de X",
    "quién fue X", cuando DuckDuckGo no trajo nada útil.
    """
    resp_busqueda = requests.get(
        "https://es.wikipedia.org/w/api.php",
        params={
            "action": "query",
            "list": "search",
            "srsearch": tema,
            "format": "json",
            "srlimit": 1,
        },
        headers={"User-Agent": f"{NOMBRE_IA}-chatbot/1.0"},
        timeout=8,
    )
    resp_busqueda.raise_for_status()
    resultados = resp_busqueda.json().get("query", {}).get("search", [])

    if not resultados:
        return None

    titulo = resultados[0]["title"]

    resp_resumen = requests.get(
        f"https://es.wikipedia.org/api/rest_v1/page/summary/{requests.utils.quote(titulo)}",
        headers={"User-Agent": f"{NOMBRE_IA}-chatbot/1.0"},
        timeout=8,
    )
    if resp_resumen.status_code != 200:
        return None

    datos = resp_resumen.json()
    extracto = datos.get("extract")
    if not extracto or not _es_relevante(tema, f"{titulo} {extracto}"):
        return None

    return extracto.strip()


def buscar_en_internet(pregunta):
    """
    Busca información sobre el tema combinando tres fuentes gratuitas,
    sin necesidad de ninguna API Key ni registro:
      1. Respuesta resumida de DuckDuckGo (Instant Answer API).
      2. Resultados de búsqueda web normales de DuckDuckGo.
      3. Resumen de Wikipedia en español (para preguntas factuales).
    Se detiene en la primera que devuelva algo útil.
    """
    tema = _limpiar_tema(pregunta)

    if not tema:
        return "¿Sobre qué tema quieres que busque información?"

    errores_conexion = 0

    for buscador in (_buscar_instant_answer, _buscar_html, _buscar_wikipedia):
        try:
            resultado = buscador(tema)
            if resultado:
                return resultado
        except requests.exceptions.ConnectionError:
            errores_conexion += 1
            continue
        except requests.exceptions.Timeout:
            continue
        except Exception:
            continue

    if errores_conexion == 3:
        return "No pude conectarme a internet para buscar. Revisa tu conexión."

    return f"No encontré resultados claros sobre '{tema}'. ¿Puedes reformular la pregunta?"


# ---------------------------------------------------------------------
# 2. GEMINI: IA REAL (entiende y redacta, no solo busca fragmentos)
# ---------------------------------------------------------------------

def gemini_configurado():
    return "PON_AQUI_TU" not in GEMINI_API_KEY and GEMINI_API_KEY.strip() != ""


def preguntar_a_gemini(mensaje_usuario, historial):
    """
    Le pregunta a Google Gemini, pasándole el historial reciente de la
    conversación para que tenga memoria real (no solo responda el último
    mensaje aislado, como hacía la búsqueda por fragmentos).

    'historial' es una lista de dicts {"role": "user"|"model", "text": "..."}
    con los turnos anteriores (sin incluir el mensaje actual).

    Devuelve el texto de la respuesta, o lanza una excepción si algo falla
    (para que quien llama pueda usar el respaldo de búsqueda en internet).
    """
    contenidos = []
    for turno in historial[-MAX_HISTORIAL:]:
        contenidos.append({
            "role": turno["role"],
            "parts": [{"text": turno["text"]}],
        })
    contenidos.append({"role": "user", "parts": [{"text": mensaje_usuario}]})

    payload = {
        "system_instruction": {
            "parts": [{
                "text": (
                    f"Te llamas {NOMBRE_IA}. Eres un asistente de IA conversacional "
                    "en español, amigable, claro y directo. Responde de forma natural, "
                    "como lo haría un asistente tipo ChatGPT: con razonamiento propio, "
                    "sin inventar que citas fuentes externas salvo que se te pida. "
                    "Sé conciso salvo que la pregunta requiera una respuesta más larga. "
                    "IMPORTANTE: responde siempre en texto plano, sin ningún formato "
                    "Markdown (nada de asteriscos **, guiones bajos __, almohadillas # "
                    "para títulos, ni backticks). Si necesitas hacer una lista, usa "
                    "guiones simples o números seguidos de un punto, en líneas separadas, "
                    "pero sin negritas ni otros símbolos de formato."
                )
            }]
        },
        "contents": contenidos,
    }

    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{MODELO_GEMINI}:generateContent?key={GEMINI_API_KEY}"
    )

    resp = requests.post(url, json=payload, timeout=30)

    if resp.status_code == 400:
        raise ValueError("Gemini rechazó la solicitud (400). Revisa que tu API Key sea válida.")
    if resp.status_code == 403:
        raise ValueError("Gemini rechazó el acceso (403). Revisa los permisos de tu API Key.")
    if resp.status_code == 404:
        raise ValueError(
            f"El modelo '{MODELO_GEMINI}' no existe o ya no está disponible (404). "
            "Revisa los modelos disponibles en https://aistudio.google.com/ y actualiza "
            "MODELO_GEMINI en app.py."
        )
    if resp.status_code == 429:
        raise ValueError("Se alcanzó el límite gratuito de solicitudes a Gemini por ahora.")

    resp.raise_for_status()
    datos = resp.json()

    try:
        return datos["candidates"][0]["content"]["parts"][0]["text"].strip()
    except (KeyError, IndexError):
        # A veces Gemini bloquea la respuesta por sus propios filtros de
        # seguridad; en ese caso no hay "candidates" útiles.
        razon = datos.get("promptFeedback", {}).get("blockReason")
        if razon:
            raise ValueError(f"Gemini bloqueó la respuesta ({razon}).")
        raise ValueError("Gemini no devolvió una respuesta utilizable.")


# ---------------------------------------------------------------------
# 3. MOTOR PRINCIPAL DEL CHATBOT
# ---------------------------------------------------------------------

def generar_respuesta(mensaje_usuario, historial=None, pedido_en_curso=None):
    """
    Devuelve una tupla (respuesta_texto, nuevo_pedido_en_curso, opciones).
    'nuevo_pedido_en_curso' solo se usa para el flujo guiado de compra de
    la tienda (ver tienda_asistente.py); en el resto de los casos es None.
    'opciones' es una lista corta de textos para mostrar como botones de
    respuesta rápida en el chat (o None si esa respuesta no tiene botones).
    """
    texto = mensaje_usuario.lower().strip()

    if not texto:
        return "Escribe algo para que pueda ayudarte.", None, None

    # Si había un pedido de la tienda en curso (esperando cantidad o
    # nombre), seguimos ese flujo sin importar qué otra cosa parezca
    # decir el mensaje (por ejemplo, un número suelto como "3").
    if pedido_en_curso:
        return tienda_asistente.responder_tienda(mensaje_usuario, pedido_en_curso)

    if es_saludo(texto):
        # En vez de un saludo genérico, mostramos de una vez el "menú" con
        # las categorías de la tienda (así el usuario ve qué puede pedir
        # desde el primer mensaje, sin tener que adivinar qué preguntar).
        return tienda_asistente.mensaje_bienvenida(), None, tienda_db.obtener_categorias()

    if es_despedida(texto):
        return "¡Hasta luego! Fue un gusto ayudarte.", None, None

    if es_agradecimiento(texto):
        return "¡De nada! Aquí estaré si necesitas algo más.", None, None

    for clave, respuesta in RESPUESTAS_BASE.items():
        if clave in texto:
            return respuesta, None, None

    resultado_calculo = intentar_calculo(texto)
    if resultado_calculo:
        return resultado_calculo, None, None

    resultado_fecha = responder_fecha_hora(texto)
    if resultado_fecha:
        return resultado_fecha, None, None

    # ¿Es una pregunta sobre la tienda (productos, precios, disponibilidad,
    # categorías, o intención de compra)? Si es así, consultamos DIRECTO la
    # base de datos relacional en vez de usar Gemini o buscar en internet:
    # es información real de nuestro catálogo, no conocimiento general.
    texto_normalizado = tienda_asistente.normalizar(mensaje_usuario)
    if tienda_asistente.detectar_consulta_tienda(texto_normalizado):
        return tienda_asistente.responder_tienda(mensaje_usuario)

    # A partir de aquí, la pregunta necesita conocimiento general.
    # Si hay una API Key de Gemini configurada, usamos la IA real primero.
    if gemini_configurado():
        try:
            return preguntar_a_gemini(mensaje_usuario, historial or []), None, None
        except Exception as e:
            # Mostramos el motivo real en la terminal (consola) para poder
            # diagnosticar por qué falló Gemini, en vez de fallar en
            # silencio. El chat sigue funcionando gracias al respaldo.
            print(f"[{NOMBRE_IA}] Gemini falló, usando respaldo de búsqueda. Motivo: {e}")

    # Respaldo: búsqueda de fragmentos en DuckDuckGo / Wikipedia.
    return buscar_en_internet(texto), None, None


# ---------------------------------------------------------------------
# 3. RUTAS WEB (interfaz que se abre en Chrome)
# ---------------------------------------------------------------------

@app.route("/")
def home():
    return render_template(
        "index.html",
        nombre_ia=NOMBRE_IA,
        mensaje_inicial=tienda_asistente.mensaje_bienvenida(),
        categorias=tienda_db.obtener_categorias(),
    )


@app.route("/chat", methods=["POST"])
def chat():
    data = request.get_json(force=True)
    mensaje = data.get("mensaje", "")

    historial = session.get("historial", [])
    pedido_en_curso = session.get("pedido_en_curso")

    respuesta, nuevo_pedido, opciones = generar_respuesta(mensaje, historial, pedido_en_curso)

    # Guardamos este turno en la memoria de la conversación (por navegador).
    historial.append({"role": "user", "text": mensaje})
    historial.append({"role": "model", "text": respuesta})
    session["historial"] = historial[-MAX_HISTORIAL:]

    # Guardamos (o borramos) el estado del pedido en curso de la tienda,
    # para poder continuar el flujo de compra en el siguiente mensaje.
    if nuevo_pedido:
        session["pedido_en_curso"] = nuevo_pedido
    else:
        session.pop("pedido_en_curso", None)

    return jsonify({"respuesta": respuesta, "opciones": opciones})


@app.route("/nueva-conversacion", methods=["POST"])
def nueva_conversacion():
    """Borra la memoria de la conversación actual (empezar de cero)."""
    session.pop("historial", None)
    session.pop("pedido_en_curso", None)
    return jsonify({"ok": True})


@app.route("/politica-privacidad")
def politica_privacidad():
    """
    Política de tratamiento de datos personales (Ley 1581 de 2012 -
    Habeas Data de Colombia), enlazada desde el mensaje de bienvenida del
    chatbot antes de registrar cualquier dato de un cliente.
    """
    return render_template("politica.html", nombre_ia=NOMBRE_IA)


# ---------------------------------------------------------------------
# 4. PANEL ADMINISTRATIVO (Dashboard) para el personal del CBA
# ---------------------------------------------------------------------
# Permite ver los pedidos registrados por el chatbot, actualizar el stock
# de los productos y cambiar el estado de un pedido (Pendiente/Entregado).
# Protegido con autenticación básica HTTP (usuario y clave abajo).

ADMIN_USUARIO = "admin"
ADMIN_CLAVE = "CBA2026"


def requiere_login_admin(vista):
    @wraps(vista)
    def envoltura(*args, **kwargs):
        auth = request.authorization
        if not auth or auth.username != ADMIN_USUARIO or auth.password != ADMIN_CLAVE:
            return Response(
                "Acceso restringido al personal del Centro de Biotecnología Agropecuaria.",
                401,
                {"WWW-Authenticate": 'Basic realm="Panel Dahian"'},
            )
        return vista(*args, **kwargs)
    return envoltura


@app.route("/admin")
@requiere_login_admin
def admin_panel():
    return render_template(
        "admin.html",
        nombre_ia=NOMBRE_IA,
        productos=tienda_db.obtener_todos(),
        pedidos=tienda_db.obtener_pedidos(),
    )


@app.route("/admin/actualizar-stock", methods=["POST"])
@requiere_login_admin
def admin_actualizar_stock():
    producto_id = request.form.get("producto_id", type=int)
    nuevo_stock = request.form.get("nuevo_stock", type=int)
    if producto_id is not None and nuevo_stock is not None:
        tienda_db.actualizar_stock(producto_id, nuevo_stock)
    return redirect(url_for("admin_panel"))


@app.route("/admin/actualizar-precio", methods=["POST"])
@requiere_login_admin
def admin_actualizar_precio():
    producto_id = request.form.get("producto_id", type=int)
    nuevo_precio = request.form.get("nuevo_precio", type=int)
    if producto_id is not None and nuevo_precio is not None:
        tienda_db.actualizar_precio(producto_id, nuevo_precio)
    return redirect(url_for("admin_panel"))


@app.route("/admin/actualizar-pedido", methods=["POST"])
@requiere_login_admin
def admin_actualizar_pedido():
    pedido_id = request.form.get("pedido_id", type=int)
    nuevo_estado = request.form.get("nuevo_estado", "")
    if pedido_id is not None and nuevo_estado:
        tienda_db.actualizar_estado_pedido(pedido_id, nuevo_estado)
    return redirect(url_for("admin_panel"))


if __name__ == "__main__":
    print("=" * 50)
    print(f" {NOMBRE_IA} iniciada.")
    print(f" Base de datos de la tienda: {tienda_db.RUTA_DB}")
    print(" Abre Chrome en: http://127.0.0.1:5000")
    print("=" * 50)
    app.run(debug=True, port=5000)
