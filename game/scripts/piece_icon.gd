extends Control

const Pieces = preload("res://scripts/pieces.gd")

var piece := "":
	set(v):
		piece = v
		queue_redraw()


func _draw() -> void:
	draw_rect(Rect2(Vector2.ZERO, size), Color("#161b27"))
	if piece == "":
		draw_string(get_theme_default_font(), Vector2(0, size.y / 2 + 6), "—", HORIZONTAL_ALIGNMENT_CENTER, size.x, 16, Color(1, 1, 1, 0.3))
	else:
		Pieces.draw_piece(self, piece, Rect2(Vector2(6, 6), size - Vector2(12, 12)))
