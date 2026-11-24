## Attacker piece button: icon, hotkey and the one-ply "pressure" hint bar.
extends Button

const Pieces = preload("res://scripts/pieces.gd")

var piece := "T"
var pressure = null  # float 0..1 (1 = hardest for the AI) or null
var show_pressure := true


func _ready() -> void:
	focus_mode = Control.FOCUS_NONE
	custom_minimum_size = Vector2(56, 70)
	tooltip_text = "出题：%s（快捷键 %s）" % [piece, piece]


func set_info(p, bar_visible: bool) -> void:
	pressure = p
	show_pressure = bar_visible
	queue_redraw()


func _draw() -> void:
	var a := 0.25 if disabled else 1.0
	Pieces.draw_piece(self, piece, Rect2(Vector2(6, 6), Vector2(size.x - 12, size.y - 30)), a)
	var font := get_theme_default_font()
	draw_string(font, Vector2(6, size.y - 10), piece, HORIZONTAL_ALIGNMENT_LEFT, -1, 13, Color(1, 1, 1, 0.55 * a))
	if show_pressure and pressure != null and not disabled:
		var p: float = clampf(float(pressure), 0.0, 1.0)
		var bar := Rect2(Vector2(22, size.y - 17), Vector2(size.x - 28, 6))
		draw_rect(bar, Color(1, 1, 1, 0.08))
		var col := Color("#5fd068").lerp(Color("#ef5350"), p)
		draw_rect(Rect2(bar.position, Vector2(maxf(2.0, bar.size.x * p), bar.size.y)), col)
