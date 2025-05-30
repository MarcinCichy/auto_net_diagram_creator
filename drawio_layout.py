# drawio_layout.py
import logging
from typing import List, Tuple, Dict, Any

logger = logging.getLogger(__name__)

def calculate_grid_layout(
        num_items: int,
        item_width: float,
        item_height: float,
        config: Dict[str, Any] # Parametr config jest już obecny
) -> List[Tuple[float, float]]:
    """
    Oblicza pozycje (x, y) dla lewego górnego rogu każdego elementu w siatce.
    Pobiera ustawienia layoutu z obiektu config.
    """
    positions: List[Tuple[float, float]] = []
    if num_items <= 0:
        logger.warning("calculate_grid_layout wywołane z num_items <= 0. Zwracam pustą listę pozycji.")
        return positions

    # Pobieranie wartości z konfiguracji; config_loader zapewni wartości domyślne
    items_per_row = config.get('devices_per_row')
    margin_x = config.get('grid_margin_x')
    margin_y = config.get('grid_margin_y')
    start_offset_x = config.get('grid_start_offset_x')
    start_offset_y = config.get('grid_start_offset_y')


    if items_per_row <= 0: # Walidacja, chociaż config_loader powinien dać sensowną domyślną
        logger.warning(f"Wartość 'devices_per_row' ({items_per_row}) jest nieprawidłowa. Ustawiam na 1.")
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
        if col_count < items_per_row:
            current_x += item_width + margin_x
        else:
            current_x = start_offset_x
            current_y += item_height + margin_y
            col_count = 0

    return positions