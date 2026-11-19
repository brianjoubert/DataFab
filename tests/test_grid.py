from database_4 import calculate_grid_position, row_letters


def grid(n_rows, n_cols, w=10.0, h=2.0, pitch_x=12.0, pitch_y=5.0, jitter=0.0):
    rects = []
    for r in range(n_rows):
        for c in range(n_cols):
            x0 = c * pitch_x
            y0 = -r * pitch_y + (jitter if c % 2 else 0.0)
            rects.append((x0, y0, x0 + w, y0 + h))
    return rects


def test_row_letters():
    assert [row_letters(i) for i in (0, 25, 26, 27, 51, 52)] == ["A", "Z", "AA", "AB", "AZ", "BA"]


def test_labels_top_to_bottom_left_to_right():
    rects = grid(2, 3)
    labels = calculate_grid_position(rects, 0.1)
    assert labels[rects[0]].label == "A1"
    assert labels[rects[2]].label == "A3"
    assert labels[rects[3]].label == "B1"
    assert (labels[rects[5]].row, labels[rects[5]].col) == (2, 3)


def test_more_than_26_rows():
    rects = grid(30, 1)
    labels = {g.label for g in calculate_grid_position(rects, 0.1).values()}
    assert {"Z1", "AA1", "AD1"} <= labels
    assert len(labels) == 30


def test_small_misalignment_stays_in_same_row():
    # Dies in the same row offset by 0.01 µm used to be split into separate rows.
    rects = grid(3, 4, jitter=0.01)
    labels = calculate_grid_position(rects, 0.1)
    assert max(g.row for g in labels.values()) == 3
    assert max(g.col for g in labels.values()) == 4


def test_empty():
    assert calculate_grid_position([], 0.1) == {}
