import requests
from bs4 import BeautifulSoup
from typing import List
from urllib.parse import urljoin

# Se importa el modelo Pydantic desde la ubicación que especificaste.
# Este script ahora asume que ese archivo y clase existen en tu proyecto.
from app.models.search_item import SearchItem

# -----------------------------------------------------------------------------
# FUNCIÓN DE SCRAPING
# -----------------------------------------------------------------------------
# La función ahora utiliza `SearchItem` para estructurar y devolver los datos.

def search_coto(search_term: str, branch_id: int = 200) -> List[SearchItem]:
    """
    Busca un término en Coto Digital y extrae los productos de la primera página.

    Args:
        search_term (str): El término de búsqueda (ej. "pepsi", "galletitas").
        branch_id (int): El ID de la sucursal. Por defecto es 200.

    Returns:
        List[SearchItem]: Una lista de objetos SearchItem con la información encontrada.
    
    Raises:
        requests.exceptions.RequestException: Si ocurre un error de red o HTTP.
    """
    base_url = "https://www.cotodigital.com.ar"
    search_url = f"{base_url}/sitios/cdigi/categoria"
    
    params = {
        "Ntt": search_term,
        "_dyncharset": "utf-8",
        "idSucursal": branch_id,
        "Dy": "1"
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }

    response = requests.get(search_url, params=params, headers=headers, timeout=20)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, 'html.parser')
    
    product_list_container = soup.find('ul', id='products')
    if not product_list_container:
        return []

    product_items = product_list_container.find_all('li', class_='clearfix')
    if not product_items:
        return []

    scraped_products = []
    for item in product_items:
        try:
            # --- Extracción de datos individuales ---
            barcode_container = item.find('div', id=lambda x: x and x.startswith('producto_'))
            barcode = barcode_container['id'].split('_')[1] if barcode_container else None
            
            link_tag = item.find('a', class_='product_image')
            product_url = urljoin(base_url, link_tag.get('href', '')) if link_tag else None
            
            image_tag = link_tag.find('img') if link_tag else None
            image_url = image_tag.get('src') if image_tag else None

            title_div = item.find('div', class_='descrip_full')
            title = title_div.text.strip() if title_div else "Título no encontrado"
            
            price_span = item.find('span', class_='atg_store_newPrice')
            price = price_span.text.strip() if price_span else "Precio no disponible"
            
            promotion_div = item.find('div', class_='discount_badge')
            promotion = promotion_div.text.strip() if promotion_div else None

            # Crea y añade el objeto SearchItem a la lista de resultados
            scraped_products.append(SearchItem(
                title=title,
                price=price,
                url=product_url,
                image_url=image_url,
                barcode=barcode,
                promotion=promotion
            ))
        except Exception:
            # Si un producto individual tiene un formato inesperado, lo salta y continúa.
            continue
            
    return scraped_products

