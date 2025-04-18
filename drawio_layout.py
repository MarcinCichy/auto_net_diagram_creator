# drawio_layout.py

DEFAULT_DEVICES_PER_ROW = 3
DEFAULT_MARGIN_X = 80 # Zwiększono marginesy
DEFAULT_MARGIN_Y = 150

def calculate_grid_layout(num_items: int,
                          item_width: float,
                          item_height: float,
                          items_per_row: int = DEFAULT_DEVICES_PER_ROW,
                          margin_x: float = DEFAULT_MARGIN_X,
                          margin_y: float = DEFAULT_MARGIN_Y) -> list[tuple[float, float]]:
    """
    Oblicza pozycje (x, y) dla siatki elementów. Zakłada stały rozmiar elementów.

    Args:
        num_items (int): Liczba elementów do rozmieszczenia.
        item_width (float): Szerokość pojedynczego elementu.
        item_height (float): Wysokość pojedynczego elementu.
        items_per_row (int): Maksymalna liczba elementów w rzędzie.
        margin_x (float): Poziomy odstęp między elementami.
        margin_y (float): Pionowy odstęp między rzędami.

    Returns:
        list[tuple[float, float]]: Lista krotek (x, y) dla lewego górnego rogu każdego elementu.
    """
    positions = []
    if items_per_row <= 0: items_per_row = 1
    if num_items <= 0: return []

    start_x = margin_x # Dodajmy margines początkowy
    start_y = margin_y
    current_x = start_x
    current_y = start_y

    for i in range(num_items):
        positions.append((current_x, current_y))

        # Przesuń X na następną pozycję
        current_x += item_width + margin_x

        # Sprawdź, czy przejść do nowego rzędu
        if (i + 1) % items_per_row == 0:
            current_x = start_x # Reset X do pozycji początkowej
            current_y += item_height + margin_y # Przesuń Y w dół o wysokość + margines

    return positions