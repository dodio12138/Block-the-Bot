## Display-only piece data (rules live in the C++ core).
extends RefCounted

const ORDER := "IOTSZJL"

# Spawn orientation, y-down box coordinates.
const SHAPES := {
	"I": [[0, 1], [1, 1], [2, 1], [3, 1]],
	"O": [[0, 0], [1, 0], [0, 1], [1, 1]],
	"T": [[1, 0], [0, 1], [1, 1], [2, 1]],
	"S": [[1, 0], [2, 0], [0, 1], [1, 1]],
	"Z": [[0, 0], [1, 0], [1, 1], [2, 1]],
	"J": [[0, 0], [0, 1], [1, 1], [2, 1]],
	"L": [[2, 0], [0, 1], [1, 1], [2, 1]],
}

const COLORS := {
	"I": Color("#3fc7e8"),
	"O": Color("#f2d03b"),
	"T": Color("#b45be0"),
	"S": Color("#5fd068"),
	"Z": Color("#ef5350"),
	"J": Color("#4a7cf0"),
	"L": Color("#f39a3b"),
}

const GARBAGE := Color("#5b6475")


static func color_of(p: String) -> Color:
	return COLORS.get(p, GARBAGE)


static func draw_piece(ci: CanvasItem, p: String, rect: Rect2, alpha: float = 1.0) -> void:
	if not SHAPES.has(p):
		return
	var cells: Array = SHAPES[p]
	var min_x := 9
	var max_x := 0
	var min_y := 9
	var max_y := 0
	for c in cells:
		min_x = mini(min_x, c[0])
		max_x = maxi(max_x, c[0])
		min_y = mini(min_y, c[1])
		max_y = maxi(max_y, c[1])
	var w := max_x - min_x + 1
	var h := max_y - min_y + 1
	var cs := floorf(minf(rect.size.x / 4.0, rect.size.y / 2.5))
	var origin := rect.position + (rect.size - Vector2(w * cs, h * cs)) / 2.0
	var col: Color = color_of(p)
	col.a = alpha
	for c in cells:
		var r := Rect2(origin + Vector2((c[0] - min_x) * cs, (c[1] - min_y) * cs), Vector2(cs, cs)).grow(-1.0)
		ci.draw_rect(r, col)
		ci.draw_rect(Rect2(r.position, Vector2(r.size.x, maxf(2.0, cs * 0.18))), Color(1, 1, 1, 0.25 * alpha))
