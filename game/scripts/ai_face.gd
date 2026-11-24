## The AI defender's face: mood follows the board risk and game events.
extends Control

const MOODS := {
	"calm": Color("#5fd068"),
	"think": Color("#4fa8e8"),
	"happy": Color("#9be76a"),
	"worried": Color("#f2c84b"),
	"panic": Color("#ef5350"),
	"dead": Color("#7d8597"),
	"smug": Color("#b45be0"),
}

var mood := "calm":
	set(v):
		mood = v if MOODS.has(v) else "calm"
		queue_redraw()
var _t := 0.0


func _ready() -> void:
	custom_minimum_size = Vector2(64, 64)


func _process(delta: float) -> void:
	_t += delta
	if mood in ["think", "panic", "worried"]:
		queue_redraw()


func _draw() -> void:
	var c := size / 2.0
	var r := minf(size.x, size.y) / 2.0 - 3.0
	var col: Color = MOODS[mood]
	var bob := sin(_t * 9.0) * 1.5 if mood == "panic" else 0.0
	c.y += bob
	draw_circle(c, r, col.darkened(0.55))
	draw_arc(c, r, 0, TAU, 48, col, 3.0, true)
	var ex := r * 0.38
	var ey := -r * 0.18
	var eye_r := r * 0.11
	var mouth_y := r * 0.35
	match mood:
		"dead":
			for sx in [-1.0, 1.0]:
				var p := c + Vector2(ex * sx, ey)
				draw_line(p + Vector2(-eye_r, -eye_r), p + Vector2(eye_r, eye_r), col, 2.5)
				draw_line(p + Vector2(-eye_r, eye_r), p + Vector2(eye_r, -eye_r), col, 2.5)
			draw_line(c + Vector2(-r * 0.3, mouth_y), c + Vector2(r * 0.3, mouth_y), col, 2.5)
		"think":
			var look := sin(_t * 3.0) * eye_r * 0.8
			for sx in [-1.0, 1.0]:
				draw_circle(c + Vector2(ex * sx + look, ey), eye_r, col)
			draw_line(c + Vector2(-r * 0.15, mouth_y), c + Vector2(r * 0.25, mouth_y - r * 0.05), col, 2.5)
		_:
			for sx in [-1.0, 1.0]:
				var er := eye_r * (1.35 if mood == "panic" else 1.0)
				draw_circle(c + Vector2(ex * sx, ey), er, col)
			if mood == "happy" or mood == "calm" or mood == "smug":
				var depth := 0.3 if mood == "happy" else 0.18
				draw_arc(c + Vector2(0, mouth_y - r * depth), r * 0.42, PI * 0.2, PI * 0.8, 16, col, 2.5, true)
				if mood == "smug":
					draw_line(c + Vector2(-ex - eye_r, ey - eye_r * 1.6), c + Vector2(-ex + eye_r, ey - eye_r * 2.1), col, 2.0)
			elif mood == "worried" or mood == "panic":
				draw_arc(c + Vector2(0, mouth_y + r * 0.22), r * 0.32, PI * 1.2, PI * 1.8, 16, col, 2.5, true)
				var drop := c + Vector2(r * 0.72, -r * 0.35 + fmod(_t * 18.0, r * 0.4))
				draw_circle(drop, r * 0.08, Color("#7cc8ff"))
