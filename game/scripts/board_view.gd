## Draws the AI's board. Occupancy always comes from the core (server rows);
## colours are cosmetic and tracked client-side.
extends Control

const Pieces = preload("res://scripts/pieces.gd")

var width := 10
var visible_h := 20
var total_h := 24
var hidden_show := 2
var colors: Dictionary = {}  # Vector2i -> Color
var active_cells: Array = []
var active_color := Color.WHITE
var ghost_sets: Array = []  # Array of cell arrays (dev mode outlines)
var flash_rows: Array = []
var banner := ""


func setup(ruleset: Dictionary) -> void:
	width = int(ruleset.get("width", 10))
	visible_h = int(ruleset.get("visible_height", 20))
	total_h = visible_h + int(ruleset.get("hidden_rows", 4))
	queue_redraw()


func clear_all() -> void:
	colors.clear()
	active_cells = []
	ghost_sets = []
	flash_rows = []
	banner = ""
	queue_redraw()


func reconcile(rows: Array) -> void:
	for y in range(total_h):
		var bits := int(rows[y]) if y < rows.size() else 0
		for x in range(width):
			var key := Vector2i(x, y)
			var filled := ((bits >> x) & 1) == 1
			if filled and not colors.has(key):
				colors[key] = Pieces.GARBAGE
			elif not filled and colors.has(key):
				colors.erase(key)
	queue_redraw()


func set_rows_colored(rows: Array, cells: Array, piece: String) -> void:
	# Replay helper: paint last placed cells, then trust rows.
	reconcile(rows)
	for c in cells:
		var k := Vector2i(int(c[0]), int(c[1]))
		if colors.has(k):
			colors[k] = Pieces.color_of(piece)
	queue_redraw()


## Locks cells into the colour grid; returns the indices of rows that are now full.
func lock_piece(cells: Array, piece: String) -> Array:
	for c in cells:
		colors[Vector2i(int(c[0]), int(c[1]))] = Pieces.color_of(piece)
	active_cells = []
	var full: Array = []
	for y in range(total_h):
		var n := 0
		for x in range(width):
			if colors.has(Vector2i(x, y)):
				n += 1
		if n == width:
			full.append(y)
	flash_rows = full
	queue_redraw()
	return full


func collapse_rows(full: Array) -> void:
	if full.is_empty():
		return
	var next: Dictionary = {}
	for key in colors:
		var v: Vector2i = key
		if full.has(v.y):
			continue
		var shift := 0
		for r in full:
			if int(r) < v.y:
				shift += 1
		next[Vector2i(v.x, v.y - shift)] = colors[key]
	colors = next
	flash_rows = []
	queue_redraw()


func _metrics() -> Array:
	var rows_shown := visible_h + hidden_show
	var cs := floorf(minf((size.x - 8.0) / width, (size.y - 8.0) / rows_shown))
	var ox := floorf((size.x - cs * width) / 2.0)
	var oy := floorf((size.y - cs * rows_shown) / 2.0)
	return [cs, ox, oy]


func _cell_rect(x: int, y: int, m: Array) -> Rect2:
	var cs: float = m[0]
	var top_row := visible_h + hidden_show - 1
	return Rect2(m[1] + x * cs, m[2] + (top_row - y) * cs, cs, cs)


func _draw_block(r: Rect2, col: Color) -> void:
	var b := r.grow(-1.0)
	draw_rect(b, col)
	draw_rect(Rect2(b.position, Vector2(b.size.x, maxf(2.0, b.size.y * 0.16))), Color(1, 1, 1, 0.22))
	draw_rect(Rect2(b.position + Vector2(0, b.size.y * 0.84), Vector2(b.size.x, b.size.y * 0.16)), Color(0, 0, 0, 0.18))


func _draw() -> void:
	var m := _metrics()
	var cs: float = m[0]
	var ox: float = m[1]
	var oy: float = m[2]
	var vis_rect := Rect2(ox, oy + hidden_show * cs, cs * width, cs * visible_h)
	draw_rect(Rect2(ox, oy, cs * width, hidden_show * cs), Color("#0a0d14"))
	draw_rect(vis_rect, Color("#121725"))
	for x in range(width + 1):
		draw_line(Vector2(ox + x * cs, vis_rect.position.y), Vector2(ox + x * cs, vis_rect.end.y), Color(1, 1, 1, 0.045))
	for y in range(visible_h + 1):
		var yy := vis_rect.position.y + y * cs
		draw_line(Vector2(ox, yy), Vector2(vis_rect.end.x, yy), Color(1, 1, 1, 0.045))
	draw_line(Vector2(ox, vis_rect.position.y), Vector2(vis_rect.end.x, vis_rect.position.y), Color("#ef5350", 0.55), 2.0)

	for key in colors:
		var v: Vector2i = key
		if v.y < visible_h + hidden_show:
			var col: Color = colors[key]
			if v.y >= visible_h:
				col = col.darkened(0.35)
			_draw_block(_cell_rect(v.x, v.y, m), col)
	for r in flash_rows:
		if int(r) < visible_h + hidden_show:
			var a := _cell_rect(0, int(r), m)
			draw_rect(Rect2(a.position, Vector2(cs * width, cs)), Color(1, 1, 1, 0.75))

	for gi in range(ghost_sets.size()):
		var gcol := Color("#ffd54f", 0.85) if gi == 0 else Color(1, 1, 1, 0.35)
		for c in ghost_sets[gi]:
			if int(c[1]) < visible_h + hidden_show:
				draw_rect(_cell_rect(int(c[0]), int(c[1]), m).grow(-3.0), gcol, false, 1.5)
	for c in active_cells:
		if int(c[1]) < visible_h + hidden_show:
			_draw_block(_cell_rect(int(c[0]), int(c[1]), m), active_color.lightened(0.15))

	draw_rect(vis_rect.grow(2.0), Color("#3a4256"), false, 2.0)
	if banner != "":
		var font := get_theme_default_font()
		draw_rect(Rect2(vis_rect.position + Vector2(0, vis_rect.size.y * 0.42), Vector2(vis_rect.size.x, 44)), Color(0, 0, 0, 0.6))
		draw_string(font, vis_rect.position + Vector2(0, vis_rect.size.y * 0.42 + 29), banner, HORIZONTAL_ALIGNMENT_CENTER, vis_rect.size.x, 20, Color.WHITE)
