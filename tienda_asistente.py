# -*- coding: utf-8 -*-
"""
tienda_asistente.py
====================
Módulo del "asistente de tienda" de Dahian.

Este módulo detecta cuándo el usuario está preguntando por productos
del Centro de Biotecnología Agropecuaria (catálogo, categorías,
precios, disponibilidad) y arma la respuesta consultando DIRECTAMENTE
la base de datos relacional (tienda_db.py / tienda.db), en vez de usar
texto fijo o buscar en internet.

También incluye un pequeño flujo guiado de "intención de compra": si
el usuario dice que quiere comprar algo, Dahian confirma cantidad y
nombre, y entrega un resumen de pedido (una simulación pensada para el
proyecto académico, no una pasarela de pagos real).

Cada función de respuesta devuelve TRES valores:
    (texto_respuesta, nuevo_estado_pedido, opciones)
'opciones' es una lista corta de textos (o None) pensada para mostrarse
como botones de selección rápida en el chat (como un menú de IVR/EPS:
"1. Consultar producto  2. Ver precios  3. Hacer un pedido"), para que
el usuario no tenga que escribir todo a mano y así evitar errores de
tipeo que lo saquen del flujo.
"""

import re
import unicodedata
import difflib

import tienda_db as db


# ---------------------------------------------------------------------
# Utilidades de texto
# ---------------------------------------------------------------------

def _normalizar(texto):
    forma = unicodedata.normalize("NFKD", texto.lower())
    return "".join(c for c in forma if not unicodedata.combining(c))


# Alias público: app.py usa esta función para normalizar el mensaje antes
# de preguntarle a este módulo si se trata de una consulta de tienda.
normalizar = _normalizar


PALABRAS_TIENDA = [
    "producto", "productos", "precio", "precios", "cuesta", "cuestan",
    "vale", "valen", "catalogo", "categoria", "categorias", "tienen",
    "hay disponible", "disponible", "disponibilidad", "stock", "existencia",
    "comprar", "compra", "pedido", "carrito", "tienda", "vender", "venden",
    "mas barato", "mas economico", "mas barata", "oferta",
]

# Palabras clave de cada categoría (para reconocer sinónimos frecuentes)
CATEGORIAS_SINONIMOS = {
    "Semillas certificadas": ["semilla", "semillas"],
    "Abonos y fertilizantes": ["abono", "abonos", "fertilizante", "fertilizantes", "cal agricola", "humus"],
    "Plántulas y material vegetal": ["plantula", "plantulas", "esqueje", "colino", "material vegetal"],
    "Kits y laboratorio": ["kit", "kits", "laboratorio", "microscopio", "bioseguridad"],
    "Control biológico": ["biopreparado", "biopreparados", "control biologico", "trichoderma",
                           "beauveria", "biocontrolador", "trampas", "feromonas", "plaga", "plagas"],
    "Herramientas agrícolas": ["herramienta", "herramientas", "azadon", "machete", "tijeras", "aspersora", "guantes"],
    "Huevos": ["huevo", "huevos", "cubeta"],
    "Flores": ["flor", "flores", "ramo", "rosas", "claveles", "girasol", "orquidea"],
    "Lácteos": ["lacteo", "lacteos", "leche", "yogur", "queso"],
}


# Vocabulario completo de la tienda (para tolerar pequeños errores de tipeo,
# ej. "perdido" en vez de "pedido"), armado una sola vez a partir de las
# listas de arriba.
_VOCABULARIO_TIENDA = set()
for _frase in PALABRAS_TIENDA:
    _VOCABULARIO_TIENDA.update(_frase.split())
for _sinonimos in CATEGORIAS_SINONIMOS.values():
    for _s in _sinonimos:
        _VOCABULARIO_TIENDA.update(_s.split())


def _parece_palabra_de_tienda_con_typo(texto_normalizado, cutoff=0.82):
    """
    Revisa si alguna palabra del mensaje es muy parecida (aunque no igual) a
    una palabra del vocabulario de la tienda, para no perder la intención
    del usuario por un pequeño error de tipeo (ej. escribir "perdido" en vez
    de "pedido"). Solo mira palabras de 5 letras o más, para evitar falsos
    positivos con palabras cortas.
    """
    palabras = re.findall(r"[a-z]+", texto_normalizado)
    for palabra in palabras:
        if len(palabra) < 5:
            continue
        if difflib.get_close_matches(palabra, _VOCABULARIO_TIENDA, n=1, cutoff=cutoff):
            return True
    return False


def detectar_consulta_tienda(texto_normalizado):
    """Devuelve True si el mensaje parece una pregunta sobre la tienda/catálogo."""
    if any(p in texto_normalizado for p in PALABRAS_TIENDA):
        return True
    for sinonimos in CATEGORIAS_SINONIMOS.values():
        if any(s in texto_normalizado for s in sinonimos):
            return True
    # Coincidencia directa con el nombre de algún producto de la base de datos
    for producto in db.obtener_todos():
        if _normalizar(producto["nombre"]) in texto_normalizado:
            return True
    # Último intento: palabras muy parecidas a las de la tienda (typos).
    if _parece_palabra_de_tienda_con_typo(texto_normalizado):
        return True
    return False


def _categoria_mencionada(texto_normalizado):
    for categoria, sinonimos in CATEGORIAS_SINONIMOS.items():
        if _normalizar(categoria) in texto_normalizado:
            return categoria
        if any(s in texto_normalizado for s in sinonimos):
            return categoria
    return None


def _categoria_por_nombre_exacto(texto_normalizado):
    """
    Devuelve la categoría solo si su NOMBRE COMPLETO aparece en el mensaje
    (ej. 'semillas certificadas'), a diferencia de _categoria_mencionada
    que también reconoce sinónimos sueltos. Se usa para dar prioridad a
    una pregunta por categoría completa por encima de una coincidencia
    parcial con el nombre de un solo producto.
    """
    for categoria in CATEGORIAS_SINONIMOS:
        if _normalizar(categoria) in texto_normalizado:
            return categoria
    return None


def _extraer_precio_maximo(texto_normalizado):
    """Busca un número en frases como 'menos de 50000' o 'por debajo de 30 mil'."""
    match = re.search(r"(menos de|maximo|menor a|por debajo de)\s*\$?\s*([\d\.]+)\s*(mil)?", texto_normalizado)
    if not match:
        return None
    numero = match.group(2).replace(".", "")
    valor = int(numero)
    if match.group(3):  # "mil"
        valor *= 1000
    return valor


def _linea_producto(p):
    estado = "Disponible" if p["stock"] > 0 else "Agotado"
    return f"• {p['nombre']} — {db.formatear_precio(p['precio'])} ({estado}, stock: {p['stock']})"


def _listar_productos(lista, encabezado, limite=6):
    if not lista:
        return None, None
    partes = [encabezado]
    for p in lista[:limite]:
        partes.append(_linea_producto(p))
    if len(lista) > limite:
        partes.append(f"...y {len(lista) - limite} producto(s) más. Pregúntame por una categoría específica para ver el resto.")
    partes.append("Elige un producto para ver el detalle, o toca \"Iniciar pedido\".")
    opciones = [p["nombre"] for p in lista[:limite]] + ["Iniciar pedido"]
    return "\n".join(partes), opciones


# Botones de categoría, reutilizados en varias respuestas ("menú" tipo IVR).
def _opciones_categorias():
    return list(db.obtener_categorias())


# ---------------------------------------------------------------------
# Flujo guiado de intención de compra (usa el "session" de Flask)
# ---------------------------------------------------------------------

PALABRAS_COMPRA = [
    "quiero comprar", "comprar", "quiero pedir", "hacer un pedido", "hacer pedido",
    "deseo comprar", "me interesa comprar", "iniciar pedido", "iniciar un pedido",
    "iniciar mi pedido", "nuevo pedido", "empezar pedido", "empezar un pedido",
    "comenzar pedido", "comenzar un pedido", "quiero un pedido", "quiero hacer un pedido",
    "hagamos un pedido", "realizar un pedido", "realizar pedido",
]

# Para reconocer una respuesta corta de sí/no después de que Dahian pregunta
# "¿quieres que iniciemos un pedido de este producto?". Los botones rápidos
# envían exactamente "Sí" / "No", que ya caen dentro de estas listas.
PALABRAS_SI = [
    "si", "sí", "claro", "dale", "de una", "por supuesto", "vale", "ok",
    "bueno", "hazlo", "listo", "obvio", "va",
]
PALABRAS_NO = ["no", "no gracias", "todavia no", "todavía no", "ahora no", "despues", "después"]


def _es_si(texto_normalizado):
    return texto_normalizado in PALABRAS_SI or any(
        texto_normalizado == p or texto_normalizado.startswith(p + " ") for p in PALABRAS_SI
    )


def _es_no(texto_normalizado):
    return texto_normalizado in PALABRAS_NO or any(texto_normalizado.startswith(p) for p in PALABRAS_NO)


# Texto de autorización de tratamiento de datos personales, conforme a la
# Ley 1581 de 2012 (Habeas Data) de Colombia. Se muestra ANTES de pedir el
# nombre y el contacto del cliente, y solo si acepta se continúa guardando
# esa información en la base de datos.
AVISO_TRATAMIENTO_DATOS = (
    "Antes de continuar, necesito tu autorización para el tratamiento de "
    "datos personales (Ley 1581 de 2012 - Habeas Data). Tu nombre y datos "
    "de contacto se usarán únicamente para que un asesor del Centro de "
    "Biotecnología Agropecuaria (SENA) gestione tu pedido, y se guardan de "
    "forma segura en nuestra base de datos. Puedes consultar la política de "
    "tratamiento de datos del SENA en /politica-privacidad.\n"
    "¿Autorizas el uso de tus datos para registrar este pedido?"
)


def _detalle_producto(p):
    """Arma la ficha de un producto y deja pendiente la confirmación de compra."""
    if p["stock"] <= 0:
        # Si está agotado, no tiene sentido preguntar "¿quieres pedirlo?":
        # ni ofrecemos el botón de confirmación ni dejamos pendiente ese
        # estado, para no confundir con una pregunta que no se puede
        # responder que sí.
        respuesta = (
            f"{p['nombre']} ({p['categoria']})\n"
            f"Precio: {db.formatear_precio(p['precio'])}\n"
            "Disponibilidad: Agotado (stock: 0)\n"
            f"{p['descripcion']}\n"
            "Por ahora no hay unidades disponibles. ¿Quieres ver otro producto de la categoría "
            f"'{p['categoria']}' o de otra?"
        )
        opciones = [p["categoria"]] + [c for c in db.obtener_categorias() if c != p["categoria"]]
        return respuesta, None, opciones

    respuesta = (
        f"{p['nombre']} ({p['categoria']})\n"
        f"Precio: {db.formatear_precio(p['precio'])}\n"
        f"Disponibilidad: Disponible (stock: {p['stock']})\n"
        f"{p['descripcion']}\n"
        "¿Quieres que iniciemos un pedido de este producto?"
    )
    estado = {"paso": "confirmar_compra", "producto_id": p["id"], "producto_nombre": p["nombre"]}
    return respuesta, estado, ["Sí", "No"]


def _tokenizar(texto_normalizado):
    return set(re.findall(r"[a-z0-9]+", texto_normalizado))


def _buscar_producto_mencionado(texto_normalizado, minimo_coincidencias=1):
    """
    Encuentra el producto de la base de datos cuyo nombre coincide con el
    mensaje, comparando PALABRAS COMPLETAS (no subcadenas). Comparar
    subcadenas causaba falsos positivos como que "certificada" (parte del
    nombre de un producto) coincidiera con "certificadas" (plural escrito
    por el usuario), hacía que preguntar por toda la categoría "semillas
    certificadas" devolviera un solo producto al azar en vez de la lista
    completa de la categoría.
    """
    tokens_texto = _tokenizar(texto_normalizado)
    mejor = None
    for p in db.obtener_todos():
        nombre_norm = _normalizar(p["nombre"])
        # Además de palabras de 4+ letras, se aceptan los códigos de calibre
        # de huevo "aa"/"aaa" como palabras válidas para distinguir entre
        # esos productos aunque sean cortas.
        palabras_nombre = [
            w for w in re.findall(r"[a-z0-9]+", nombre_norm)
            if len(w) >= 4 or re.fullmatch(r"a{2,3}", w)
        ]
        palabras_coincidentes = [w for w in palabras_nombre if w in tokens_texto]
        coincidencias = len(palabras_coincidentes)
        # Una sola palabra larga y poco común (7+ letras, ej. "bioseguridad"
        # o "microscopio") ya identifica el producto sin ambigüedad, aunque
        # el resto del nombre (kit, de...) no aparezca en el mensaje.
        tiene_palabra_distintiva = any(len(w) >= 7 for w in palabras_coincidentes)
        if (coincidencias >= minimo_coincidencias or tiene_palabra_distintiva) and (
            mejor is None or coincidencias > mejor[0]
        ):
            mejor = (coincidencias, p)
    return mejor[1] if mejor else None


def _iniciar_flujo_compra(texto_normalizado):
    producto = _buscar_producto_mencionado(texto_normalizado)
    if not producto:
        categorias = ", ".join(db.obtener_categorias())
        return (
            "Con gusto te ayudo a hacer un pedido. ¿Qué producto te interesa? "
            f"Estas son nuestras categorías: {categorias}."
        ), None, _opciones_categorias()

    if producto["stock"] <= 0:
        return (
            f"'{producto['nombre']}' está agotado por ahora. "
            "¿Quieres que te muestre otro producto parecido de la misma categoría?"
        ), None, _opciones_categorias()

    estado = {"paso": "cantidad", "producto_id": producto["id"], "producto_nombre": producto["nombre"]}
    return (
        f"Perfecto, '{producto['nombre']}' cuesta {db.formatear_precio(producto['precio'])} "
        f"y tenemos {producto['stock']} unidades disponibles. ¿Cuántas unidades quieres pedir?"
    ), estado, ["1", "2", "3", "5", "10"]


def _continuar_flujo_compra(texto_original, estado):
    paso = estado.get("paso")

    if paso == "confirmar_compra":
        texto_normalizado = _normalizar(texto_original).replace(",", " ").strip(" .!¡")
        texto_normalizado = re.sub(r"\s+", " ", texto_normalizado).strip()

        if _es_si(texto_normalizado):
            conexion = db.conectar()
            fila = conexion.execute("SELECT * FROM productos WHERE id = ?", (estado["producto_id"],)).fetchone()
            conexion.close()
            if not fila or fila["stock"] <= 0:
                return "Lo siento, ese producto ya no está disponible. ¿Quieres ver otro?", None, _opciones_categorias()
            producto = dict(fila)
            nuevo_estado = {"paso": "cantidad", "producto_id": producto["id"], "producto_nombre": producto["nombre"]}
            return (
                f"¿Cuántas unidades de '{producto['nombre']}' quieres pedir? "
                f"(tenemos {producto['stock']} disponibles)"
            ), nuevo_estado, ["1", "2", "3", "5", "10"]

        if _es_no(texto_normalizado):
            return "Entendido, no inicio el pedido. ¿En qué más te puedo ayudar?", None, _opciones_categorias()

        # El mensaje no fue un sí/no claro: lo tratamos como una consulta
        # nueva de la tienda (por ejemplo, preguntó por otro producto).
        return responder_tienda(texto_original)

    if paso == "cantidad":
        match = re.search(r"\d+", texto_original)
        if not match:
            return "No entendí la cantidad. ¿Cuántas unidades quieres pedir? (escribe solo el número)", estado, ["1", "2", "3", "5", "10"]

        cantidad = int(match.group(0))
        conexion = db.conectar()
        fila = conexion.execute("SELECT * FROM productos WHERE id = ?", (estado["producto_id"],)).fetchone()
        conexion.close()

        if not fila:
            return "Hubo un problema encontrando ese producto, ¿quieres intentar de nuevo?", None, _opciones_categorias()
        producto = dict(fila)

        if cantidad <= 0:
            return "La cantidad debe ser mayor a cero. ¿Cuántas unidades quieres pedir?", estado, ["1", "2", "3", "5", "10"]
        if cantidad > producto["stock"]:
            return (
                f"Solo tenemos {producto['stock']} unidades disponibles de '{producto['nombre']}'. "
                "¿Qué cantidad (igual o menor) quieres pedir?"
            ), estado, None

        total = cantidad * producto["precio"]
        nuevo_estado = {
            "paso": "consentimiento",
            "producto_id": producto["id"],
            "producto_nombre": producto["nombre"],
            "cantidad": cantidad,
            "total": total,
        }
        return (
            f"Anotado: {cantidad} unidad(es) de '{producto['nombre']}' — total {db.formatear_precio(total)}.\n"
            + AVISO_TRATAMIENTO_DATOS
        ), nuevo_estado, ["Sí, autorizo", "No autorizo"]

    if paso == "consentimiento":
        texto_normalizado = _normalizar(texto_original).replace(",", " ").strip(" .!¡")
        texto_normalizado = re.sub(r"\s+", " ", texto_normalizado).strip()

        if _es_si(texto_normalizado):
            nuevo_estado = dict(estado)
            nuevo_estado["paso"] = "nombre"
            return "Gracias por autorizarlo. ¿A nombre de quién registro el pedido?", nuevo_estado, None

        if _es_no(texto_normalizado):
            return (
                "Sin tu autorización no puedo registrar el pedido, ya que necesito guardar tu "
                "nombre y contacto para que un asesor te confirme la entrega. "
                "¿Quieres seguir viendo el catálogo de todas formas?"
            ), None, _opciones_categorias()

        return (
            "Para continuar necesito que confirmes si autorizas o no el tratamiento de tus datos "
            "personales (responde \"Sí, autorizo\" o \"No autorizo\")."
        ), estado, ["Sí, autorizo", "No autorizo"]

    if paso == "nombre":
        nombre_cliente = texto_original.strip()
        if not nombre_cliente:
            return "No entendí el nombre. ¿A nombre de quién registro el pedido?", estado, None
        nuevo_estado = dict(estado)
        nuevo_estado["paso"] = "contacto"
        nuevo_estado["cliente_nombre"] = nombre_cliente
        return (
            "Para que un asesor del Centro pueda confirmarte la entrega, escríbeme un número de "
            "teléfono/WhatsApp o un correo electrónico de contacto."
        ), nuevo_estado, None

    if paso == "contacto":
        contacto = texto_original.strip()
        tiene_digitos = bool(re.search(r"\d{7,}", contacto))
        parece_correo = "@" in contacto and "." in contacto
        if not (tiene_digitos or parece_correo):
            return (
                "Ese dato no parece un teléfono ni un correo válido. Escríbeme un número de "
                "teléfono/WhatsApp (mínimo 7 dígitos) o un correo electrónico."
            ), estado, None

        pedido_id = db.crear_pedido(
            producto_id=estado["producto_id"],
            producto_nombre=estado["producto_nombre"],
            cantidad=estado["cantidad"],
            total=estado["total"],
            cliente_nombre=estado["cliente_nombre"],
            cliente_contacto=contacto,
        )
        resumen = (
            f"Pedido #{pedido_id} registrado:\n"
            f"• Cliente: {estado['cliente_nombre']}\n"
            f"• Contacto: {contacto}\n"
            f"• Producto: {estado['producto_nombre']}\n"
            f"• Cantidad: {estado['cantidad']}\n"
            f"• Total: {db.formatear_precio(estado['total'])}\n"
            "Un asesor del Centro de Biotecnología Agropecuaria se comunicará contigo para "
            "confirmar la entrega. ¿Deseas consultar otro producto?"
        )
        nuevo_estado = {"paso": "post_pedido"}
        return resumen, nuevo_estado, ["Sí", "No"]

    if paso == "post_pedido":
        texto_normalizado = _normalizar(texto_original).replace(",", " ").strip(" .!¡")
        texto_normalizado = re.sub(r"\s+", " ", texto_normalizado).strip()

        if _es_si(texto_normalizado):
            return mensaje_bienvenida(), None, _opciones_categorias()

        if _es_no(texto_normalizado):
            return "¡Con gusto! Gracias por tu compra. Aquí estaré si necesitas algo más.", None, None

        # No fue un sí/no claro: en vez de perder el contexto (y caer en una
        # búsqueda genérica sin relación), lo tratamos como una nueva
        # consulta de la tienda, por ejemplo el nombre de otro producto.
        return responder_tienda(texto_original)

    return "¿En qué más te puedo ayudar con la tienda?", None, _opciones_categorias()


def mensaje_bienvenida():
    """
    Mensaje inicial de Dahian: en vez de un saludo genérico, muestra de
    una vez el 'menú' con las categorías de la tienda, para que el
    usuario sepa desde el primer momento qué puede consultar.
    """
    categorias = db.obtener_categorias()
    partes = [
        "¡Hola! Soy Dahian, el asistente virtual de la tienda del "
        "Centro de Biotecnología Agropecuaria (SENA).",
        "",
        "Estas son nuestras categorías de productos:",
    ]
    partes.extend(f"• {c}" for c in categorias)
    partes.append("")
    partes.append("Toca una categoría, o escríbeme el producto que buscas.")
    partes.append(
        "Si registras un pedido, tus datos se tratan conforme a nuestra política de "
        "tratamiento de datos personales: /politica-privacidad"
    )
    return "\n".join(partes)


# ---------------------------------------------------------------------
# Función principal del módulo
# ---------------------------------------------------------------------

def responder_tienda(texto_original, pedido_en_curso=None):
    """
    Punto de entrada del asistente de tienda.

    Devuelve una tupla (respuesta_texto, nuevo_estado_pedido, opciones).
    'nuevo_estado_pedido' debe guardarse en la sesión del usuario
    (o None si no hay un pedido en curso) para poder continuar el
    flujo de compra en el siguiente mensaje. 'opciones' es una lista de
    textos cortos para mostrar como botones de respuesta rápida (o None
    si no aplica ningún botón para esa respuesta).
    """
    texto_normalizado = _normalizar(texto_original)

    # Si ya había un pedido en curso esperando cantidad o nombre, seguimos ahí.
    if pedido_en_curso:
        return _continuar_flujo_compra(texto_original, pedido_en_curso)

    # ¿El usuario quiere iniciar una compra?
    if any(frase in texto_normalizado for frase in PALABRAS_COMPRA):
        return _iniciar_flujo_compra(texto_normalizado)

    # ¿Pregunta por el catálogo completo o las categorías?
    if "catalogo" in texto_normalizado or ("que productos" in texto_normalizado) or ("categorias" in texto_normalizado):
        categorias = db.obtener_categorias()
        partes = ["Estas son las categorías disponibles en la tienda del Centro de Biotecnología Agropecuaria:"]
        partes.extend(f"• {c}" for c in categorias)
        partes.append("Toca una categoría para ver sus productos.")
        return "\n".join(partes), None, _opciones_categorias()

    # ¿Pregunta por el producto más barato / una oferta?
    if "mas barato" in texto_normalizado or "mas economico" in texto_normalizado or "mas barata" in texto_normalizado:
        categoria = _categoria_mencionada(texto_normalizado)
        base = db.buscar_por_categoria(categoria) if categoria else None
        producto = db.producto_mas_barato(base)
        if not producto:
            return "No encontré productos para comparar precios en este momento.", None, _opciones_categorias()
        return (
            f"El producto más económico disponible es '{producto['nombre']}' "
            f"a {db.formatear_precio(producto['precio'])} ({producto['descripcion']})."
        ), None, ["Iniciar pedido"] + _opciones_categorias()

    # ¿Pregunta con un tope de precio ("menos de 50000")?
    precio_maximo = _extraer_precio_maximo(texto_normalizado)
    if precio_maximo is not None:
        categoria = _categoria_mencionada(texto_normalizado)
        base = db.buscar_por_categoria(categoria) if categoria else db.obtener_todos()
        filtrados = db.filtrar_por_precio_maximo(precio_maximo, base)
        respuesta, opciones = _listar_productos(
            filtrados, f"Productos con precio menor o igual a {db.formatear_precio(precio_maximo)}:"
        )
        if not respuesta:
            return f"No encontré productos por {db.formatear_precio(precio_maximo)} o menos.", None, _opciones_categorias()
        return respuesta, None, opciones

    # ¿El mensaje nombra una categoría COMPLETA (ej. "semillas certificadas")?
    # Esto tiene prioridad sobre la coincidencia con un solo producto: si
    # preguntan por toda la categoría, deben ver la lista completa, no un
    # producto elegido al azar dentro de ella.
    categoria_exacta = _categoria_por_nombre_exacto(texto_normalizado)
    if categoria_exacta:
        productos = db.buscar_por_categoria(categoria_exacta)
        if "disponible" in texto_normalizado or "hay" in texto_normalizado.split():
            productos = db.productos_disponibles(productos)
        respuesta, opciones = _listar_productos(productos, f"Esto tenemos en '{categoria_exacta}':")
        if not respuesta:
            return f"No tengo productos registrados en la categoría '{categoria_exacta}' en este momento.", None, _opciones_categorias()
        return respuesta, None, opciones

    # ¿Menciona un producto específico por nombre (con al menos 2 palabras
    # coincidentes, para no confundirlo con una simple mención de categoría)?
    producto_encontrado = _buscar_producto_mencionado(texto_normalizado, minimo_coincidencias=2)
    if producto_encontrado:
        return _detalle_producto(producto_encontrado)

    # ¿Menciona una categoría conocida (por sinónimo)?
    categoria = _categoria_mencionada(texto_normalizado)
    if categoria:
        productos = db.buscar_por_categoria(categoria)
        if "disponible" in texto_normalizado or "hay" in texto_normalizado.split():
            productos = db.productos_disponibles(productos)
        respuesta, opciones = _listar_productos(productos, f"Esto tenemos en '{categoria}':")
        if not respuesta:
            return f"No tengo productos registrados en la categoría '{categoria}' en este momento.", None, _opciones_categorias()
        return respuesta, None, opciones

    # ¿Menciona un producto específico por nombre (coincidencia más suelta,
    # por si el mensaje solo repite una palabra del nombre)?
    producto_encontrado = _buscar_producto_mencionado(texto_normalizado)
    if producto_encontrado:
        return _detalle_producto(producto_encontrado)

    # Búsqueda genérica por texto libre contra nombre/descripción
    resultados = db.buscar_por_nombre(texto_normalizado)
    if resultados:
        respuesta, opciones = _listar_productos(resultados, "Esto encontré relacionado con tu búsqueda:")
        if respuesta:
            return respuesta, None, opciones

    categorias = ", ".join(db.obtener_categorias())
    return (
        "No encontré un producto exacto con esos datos en nuestra base de datos. "
        f"Estas son nuestras categorías disponibles: {categorias}. "
        "¿Sobre cuál te gustaría preguntar?"
    ), None, _opciones_categorias()
