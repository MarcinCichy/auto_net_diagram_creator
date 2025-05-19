# drawio_layout.py
import logging
from typing import List, Tuple

logger = logging.getLogger(__name__)

DEFAULT_DEVICES_PER_ROW = 3
DEFAULT_MARGIN_X = 450  # Poziomy odstęp między urządzeniami ORAZ margines od krawędzi diagramu
DEFAULT_MARGIN_Y = 350  # Pionowy odstęp między urządzeniami ORAZ margines od krawędzi diagramu


def calculate_grid_layout(
        num_items: int,
        item_width: float,  # Maksymalna szerokość elementu
        item_height: float,  # Maksymalna wysokość elementu
        items_per_row: int = DEFAULT_DEVICES_PER_ROW,
        margin_x: float = DEFAULT_MARGIN_X,  # Odstęp między kolumnami
        margin_y: float = DEFAULT_MARGIN_Y,  # Odstęp między rzędami
        start_offset_x: float = DEFAULT_MARGIN_X / 2,  # Początkowy margines od lewej krawędzi diagramu
        start_offset_y: float = DEFAULT_MARGIN_Y / 2  # Początkowy margines od górnej krawędzi diagramu
) -> List[Tuple[float, float]]:
    """
    Oblicza pozycje (x, y) dla lewego górnego rogu każdego elementu w siatce.
    """
    positions: List[Tuple[float, float]] = []
    if num_items <= 0:
        logger.warning("calculate_grid_layout wywołane z num_items <= 0. Zwracam pustą listę pozycji.")
        return positions
    if items_per_row <= 0:
        logger.warning("items_per_row musi być większe od 0. Ustawiam na 1.")
        items_per_row = 1

    logger.debug(
        f"Obliczanie layoutu siatki dla {num_items} elementów. "
        f"Rozmiar elementu (max): {item_width:.0f}x{item_height:.0f}. "
        f"Elementów na rząd: {items_per_row}. Marginesy X/Y: {margin_x}/{margin_y}. "
        f"Offset startowy X/Y: {start_offset_x}/{start_offset_y}."
    )

    current_x = start_offset_x
    current_y = start_offset_y

    col_count = 0
    for i in range(num_items):
        positions.append((current_x, current_y))
        logger.debug(f"  Pozycja dla elementu {i + 1}: ({current_x:.0f}, {current_y:.0f})")

        col_count += 1
        if col_count < items_per_row:  # Jeśli nie ostatni element w rzędzie (lub jedyny)
            current_x += item_width + margin_x  # Przesuń w prawo
        else:  # Ostatni element w rzędzie, przejdź do nowego rzędu
            current_x = start_offset_x  # Reset X
            current_y += item_height + margin_y  # Przesuń Y w dół
            col_count = 0

    return positions