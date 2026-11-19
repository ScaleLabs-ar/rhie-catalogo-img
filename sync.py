#!/usr/bin/env python3
"""Reconstruye el catalogo brandeado de RHIE: hornea lo nuevo y reescribe el feed.

Corre solo, cada hora, por GitHub Actions. No depende de ninguna maquina ni del
token de la API de Tiendanube: los datos salen de las paginas publicas.

Que hace, en orden:
  1. Lee el sitemap y cada pagina de producto.
  2. Hornea el marco sobre las fotos que todavia no estan en img/.
  3. Escribe img/index.json y feed_brandeado.csv.

Es idempotente: el nombre del archivo horneado es el hash de la URL original,
asi que una foto que ya se horneo no se vuelve a bajar ni a procesar.

El id del feed es el id de VARIANTE de Tiendanube. Se verifico el 19/11/2026
leyendo la ficha del Difusor Amber Oud: el pixel manda
`fb_params['content_ids'] = [productVariantId]` con el valor 1575588612, que es
el mismo retailer_id que ya tiene el catalogo original. Si el feed usara otro
id, Meta no podria atribuir conversiones a productos y el catalogo quedaria
ciego.

EL HORNEADO NO RECORTA. Las fotos de RHIE son verticales 2:3 sobre un fondo
degradado (taupe arriba, mesa blanca abajo) y el catalogo se ve en 1:1. Se
probaron las tres salidas:
  recortar  el producto llena la tarjeta pero se corta parte de la foto.
            Descartado por Alvaro el 19/11/2026: no se corta producto.
  entera    la foto completa y chica sobre fondo plano. Desperdicia media
            tarjeta y se le ven los bordes.
  banda     se replica la columna del borde a los costados. ELEGIDA. No corta
            nada, el producto queda grande y la costura no se ve porque el
            fondo es un degradado suave. Un color plano si dejaria una linea.
"""
import csv
import hashlib
import html
import io
import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from PIL import Image

TIENDA = "https://rhie.com.ar"
BASE_IMG = "https://scalelabs-ar.github.io/rhie-catalogo-img/img/"
DIR = os.path.dirname(os.path.abspath(__file__))
IMGS = os.path.join(DIR, "img")
MARCO = os.path.join(DIR, "marco.png")
UA = {"User-Agent": "Mozilla/5.0"}
LIENZO = 1080

# Tiendanube corta cuando le pegas fuerte: con 6 hilos, 277 de 355 paginas de G4
# volvieron con HTTPError aunque cada una servia bien de a una. Concurrencia
# baja y reintento con backoff.
HILOS = 3
REINTENTOS = 4

slug = lambda u: hashlib.sha1(u.encode()).hexdigest()[:16]


def bajar(url, binario=False):
    ultimo = None
    for intento in range(REINTENTOS):
        try:
            d = urllib.request.urlopen(
                urllib.request.Request(url, headers=UA), timeout=60).read()
            return d if binario else d.decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            ultimo = e
            if e.code == 404:
                raise                      # no existe: no tiene sentido insistir
        except Exception as e:
            ultimo = e
        time.sleep((2 ** intento) + random.uniform(0, 1))
    raise ultimo


# ---------------------------------------------------------------- horneado ---

def cuadrar(foto):
    """Lleva la foto a 1080x1080 replicando la columna del borde. No recorta."""
    foto = foto.convert("RGB")
    w, h = foto.size
    nw = max(1, round(w * LIENZO / h))
    foto = foto.resize((nw, LIENZO), Image.LANCZOS)
    if nw >= LIENZO:                       # foto apaisada: ahi si hay que recortar
        x = (nw - LIENZO) // 2
        return foto.crop((x, 0, x + LIENZO, LIENZO))
    a = np.array(foto)
    falta = LIENZO - nw
    izq, der = falta // 2, falta - falta // 2
    a = np.concatenate(
        [np.repeat(a[:, :1], izq, axis=1), a, np.repeat(a[:, -1:], der, axis=1)],
        axis=1,
    )
    return Image.fromarray(a)


def hornear(url, marco):
    destino = os.path.join(IMGS, slug(url) + ".jpg")
    if os.path.exists(destino):
        return "ya estaba"
    try:
        foto = Image.open(io.BytesIO(bajar(url, binario=True)))
    except Exception as e:
        print("  ERROR bajando", type(e).__name__, url)
        return "error"

    lienzo = cuadrar(foto).convert("RGBA")
    lienzo.alpha_composite(marco)
    lienzo.convert("RGB").save(destino, quality=88, optimize=True)
    return "generada"


# -------------------------------------------------------------- la tienda ---

def limpiar(t, limite):
    t = " ".join(re.sub(r"<[^>]+>", " ", html.unescape(t or "")).split())
    return t[: limite - 1] + "…" if len(t) > limite else t


def urls_de_productos():
    """Los sitemaps de RHIE viven en el CDN de Tiendanube, no en el dominio.
    Se leen de robots.txt para no hardcodear una URL que cambia con el theme."""
    robots = bajar(f"{TIENDA}/robots.txt")
    mapas = re.findall(r"(?i)^\s*Sitemap:\s*(\S+)", robots, flags=re.M)
    urls = set()
    for m in mapas:
        if "blog" in m:
            continue
        try:
            d = urllib.request.urlopen(
                urllib.request.Request(m, headers=UA), timeout=60).read()
            if d[:2] == b"\x1f\x8b":
                import gzip
                d = gzip.decompress(d)
            urls |= set(re.findall(r"https://rhie\.com\.ar/productos/[a-z0-9\-]+/",
                                   d.decode("utf-8", "replace")))
        except Exception as e:
            print("  ERROR leyendo sitemap", m, type(e).__name__)
    return sorted(urls)


def leer_producto(url):
    try:
        h = bajar(url)
    except Exception as e:
        print(f"  ERROR {type(e).__name__} {getattr(e, 'code', '')} {url}")
        return []

    def meta(prop):
        m = re.search(rf'<meta property="{prop}" content="([^"]*)"', h)
        return html.unescape(m.group(1)) if m else ""

    nombre = meta("og:title")
    desc = meta("og:description") or nombre
    cat = re.findall(r'<a[^>]+href="' + TIENDA + r'/([a-z0-9\-]+)/"[^>]*itemprop="item"', h)

    # SOLO el primer data-variants: es el de #single-product. Los que siguen son
    # los productos relacionados, y tomarlos multiplica el feed.
    m = re.search(r'data-variants="([^"]+)"', h)
    if not m:
        return []

    esp = re.search(r"productVariantId\s*=\s*'(\d+)'", h)

    filas = []
    for v in json.loads(html.unescape(m.group(1))):
        if not v.get("is_visible", True):
            continue
        img = v.get("image_url") or ""
        if img.startswith("//"):
            img = "https:" + img
        if not img:
            continue
        gestiona = v.get("stock") is not None
        disponible = (not gestiona) or (v.get("stock") or 0) > 0
        filas.append({
            "id": str(v["id"]),
            "title": limpiar(nombre, 200),
            "description": limpiar(desc, 5000),
            "availability": "in stock" if disponible and v.get("available", True)
                            else "out of stock",
            "condition": "new",
            "price": f'{v["price_number"]:.2f} ARS',
            "link": url,
            "image_link": "",                 # se completa despues del horneado
            "foto": img,
            "brand": "RHIÉ",
            "product_type": cat[0].replace("-", " ") if cat else "",
            "custom_label_0": cat[0] if cat else "",
            "quantity_to_sell_on_facebook": v.get("stock") if gestiona else "",
        })

    if esp and esp.group(1) not in [f["id"] for f in filas]:
        print(f"  OJO: el pixel manda {esp.group(1)} y el feed no lo tiene — {url}")
    return filas


def main():
    os.makedirs(IMGS, exist_ok=True)
    marco = Image.open(MARCO).convert("RGBA")
    assert marco.size == (LIENZO, LIENZO), f"El marco tiene que ser {LIENZO}x{LIENZO}"

    urls = urls_de_productos()
    print(f"productos en el sitemap: {len(urls)}")
    if not urls:
        sys.exit("ERROR: el sitemap no devolvio productos — no se toca el feed.")

    filas = [f for g in ThreadPoolExecutor(HILOS).map(leer_producto, urls) for f in g]
    if not filas:
        # Sin esto, un error transitorio de la tienda vacia el feed y Meta lo
        # levanta vacio a la hora siguiente: la campaña se cae sin aviso.
        sys.exit("ERROR: la tienda no devolvio productos — no se toca el feed.")

    fotos = sorted({f["foto"] for f in filas})
    res = list(ThreadPoolExecutor(HILOS).map(lambda u: hornear(u, marco), fotos))
    print(f"fotos: {len(fotos)} | generadas: {res.count('generada')} | "
          f"ya estaban: {res.count('ya estaba')} | errores: {res.count('error')}")

    disponibles = sorted(f[:-4] for f in os.listdir(IMGS) if f.endswith(".jpg"))
    json.dump(disponibles, open(os.path.join(IMGS, "index.json"), "w"))

    sin_marco = 0
    for f in filas:
        foto = f.pop("foto")
        if slug(foto) in disponibles:
            f["image_link"] = BASE_IMG + slug(foto) + ".jpg"
        else:
            f["image_link"] = foto      # mejor sin marco que roto
            sin_marco += 1

    cols = ["id", "title", "description", "availability", "condition", "price",
            "link", "image_link", "brand", "product_type", "custom_label_0",
            "quantity_to_sell_on_facebook"]
    with open(os.path.join(DIR, "feed_brandeado.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(filas)

    agotados = sum(1 for f in filas if f["availability"] == "out of stock")
    print(f"-> feed_brandeado.csv: {len(filas)} variantes "
          f"({agotados} sin stock, {sin_marco} sin marco)")


if __name__ == "__main__":
    main()
