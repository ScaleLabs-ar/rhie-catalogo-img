# RHIÉ — imágenes del catálogo con marco horneado

Imágenes del catálogo de [rhie.com.ar](https://rhie.com.ar) con el marco de
marca estampado encima, para los anuncios de catálogo de Meta.

El feed vive en
`https://scalelabs-ar.github.io/rhie-catalogo-img/feed_brandeado.csv` y las
imágenes en `.../img/<hash>.jpg`.

## Cómo funciona

`sync.py` corre cada hora por GitHub Actions, al :10. Lee las páginas públicas
de la tienda (no usa el token de la API de Tiendanube, que no tenemos), hornea
el marco sobre las fotos nuevas y reescribe `feed_brandeado.csv`. Meta lee el
feed al :20.

Es idempotente: el nombre del archivo horneado es el hash de la URL original de
la foto, así que una foto que ya se procesó no se vuelve a bajar.

## El id del feed

El `id` es el **id de variante de Tiendanube**, que es lo que el píxel manda en
`content_ids`. Verificado el 19/11/2026 en la ficha del Difusor Amber Oud:

```js
const productVariantId = '1575588612';
fb_params['content_ids'] = [productVariantId];
```

Ese mismo número es el `retailer_id` del producto en el catálogo original
"Catálogo de RHIE" (`835267939657733`). Si el feed usara otro id, Meta no
podría atribuir conversiones a productos y el catálogo quedaría ciego.

## El horneado no recorta

Las fotos de RHIÉ son verticales 2:3 sobre un fondo degradado (taupe arriba,
mesa blanca abajo) y el catálogo de Meta se ve en 1:1. Se probaron las tres
salidas:

| Opción | Qué hace | Veredicto |
|---|---|---|
| recortar | ventana cuadrada alrededor del producto | descartada por Álvaro el 19/11/2026: corta producto |
| entera | la foto completa y chica sobre fondo plano | descartada: desperdicia media tarjeta y se le ven los bordes |
| banda | replica la columna del borde a los costados | **elegida**: no corta nada y la costura no se ve |

La costura no se ve porque el fondo es un degradado suave. Rellenar con un
color plano sí dejaría una línea visible.

## Los claims del marco

Los tres están verificados producto por producto el 19/11/2026:

- **Envío gratis** — la etiqueta está en los 18 productos de la tienda.
- **10% OFF por transferencia** — aparece en toda ficha.
- **Hasta 3 cuotas sin interés** — 2 desde $78.000, 3 desde $116.000. El
  "hasta" cubre que no aplica a todo el catálogo.

Si alguno deja de ser cierto hay que regenerar `marco.png` y borrar `img/` para
que se hornee todo de nuevo.

## Dos guardas que no hay que sacar

`sync.py` aborta sin tocar el feed si el sitemap o la tienda devuelven cero
productos. Sin eso, un error transitorio de Tiendanube vacía el feed, Meta lo
levanta vacío a la hora siguiente y la campaña se cae sin aviso.

La concurrencia está en 3 hilos con 4 reintentos y backoff. Tiendanube corta
cuando se le pega fuerte: con 6 hilos, 277 de 355 páginas de G4 volvieron con
error aunque cada una servía bien de a una.

## Correrlo a mano

```bash
pip install Pillow numpy
python3 sync.py
```

## Lo que quedó armado en Meta (19/11/2026)

| Objeto | ID |
|---|---|
| Negocio | `1061671283703973` (Rhie) |
| Cuenta publicitaria | `1748281746538867` (RHIE MKTG, ARS) |
| Página | `1061665863704515` |
| Píxel | `970240078981302` (RHIE AROMAS) |
| Catálogo original | `835267939657733` — **no se toca** |
| Catálogo brandeado | `1134611082720414` |
| Feed horario | `2442216696309838`, lee el CSV al :20 |
| Conjunto de productos | `2521912381545634` (en stock, 16 de 21) |
| Campaña | `120254661091350202` (v2) |
| Conjunto de anuncios | `120254661092980202` |
| Anuncio | `120254661095730202` |
| Creativo | `2224119674834035` |

Primera importación: **21 de 21 productos, cero inválidos**.

### Dos trampas que costaron tiempo

**El `product_set_id` va en el ANUNCIO, no en el conjunto de anuncios.** Meta
rechaza la publicación con "La configuración del conjunto de productos cambió
de lugar". El `promoted_object` del conjunto lleva solo `pixel_id`,
`custom_event_type` y `product_catalog_id`. Ya había pasado en Plyrap.

**Los creativos son inmutables.** `ads_creative_update` solo deja cambiar
nombre, estado y etiquetas. Para apagar las mejoras de Advantage+ hubo que
crear un creativo nuevo con `degrees_of_freedom_spec` en OPT_OUT y cambiárselo
al anuncio. Si quedan prendidas, Meta redibuja y expande la imagen y arruina el
marco horneado.

### La trampa cara: Meta eligió solo el catálogo de OTRO cliente

Al crear la campaña **sin declarar catálogo**, Meta completó el
`promoted_object` con su `recommended_catalog_id`, que para esta cuenta es
**`313428176385786` — COBB Grill Argentina**, de otro negocio. El anuncio de
Rhie quedó mostrando parrillas COBB. Lo detectó Álvaro mirando la vista previa
del panel, no la API: ningún `active_errors` lo reporta.

`ads_catalog_list_dpa_eligible_catalogs` lo confirma: el catálogo de RHIÉ
figura **primero** entre los elegibles, pero `recommended_catalog_id` apunta a
COBB igual. La recomendación cruza todos los catálogos que ve la identidad
autenticada, no los del negocio de la cuenta.

**Y el `promoted_object` de una campaña publicada es inmutable**: Meta rechaza
el cambio con "Actualización del objeto promocionado no válida". No se arregla,
hay que rehacer la campaña.

Reglas que quedan:

1. **Siempre pasar `promoted_object` con el `product_catalog_id` al crear la
   campaña.** Nunca dejar que Meta lo complete.
2. **Mirar la vista previa antes de activar.** La estructura puede estar sin
   errores y el anuncio mostrar el catálogo equivocado.
3. La campaña vieja `120254660990150202` quedó eliminada. Gasto: $0 en los tres
   niveles, se detectó antes de que entregara.
