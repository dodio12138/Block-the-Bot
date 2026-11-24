## Block the Bot — main screen.
## Left: attacker controls. Centre: AI board. Right: AI strategy/risk panel. Bottom: timeline.
## User args (after `--`): --autoplay=N --screenshot=PATH --quit --dev --agent=ID --mode=free|bag7|challenge
extends Control

const Pieces = preload("res://scripts/pieces.gd")
const NetClient = preload("res://scripts/net_client.gd")
const BoardView = preload("res://scripts/board_view.gd")
const PieceButton = preload("res://scripts/piece_button.gd")
const PieceIcon = preload("res://scripts/piece_icon.gd")

const MODES := ["free", "bag7", "challenge"]
const MODE_LABELS := ["自由出题", "7-Bag 对抗", "挑战关卡"]
const SOURCE_LABELS := {
	"heuristic": "传统启发式",
	"llm": "0.6B 模型",
	"fallback": "回退（启发式）",
	"hier-local": "分层：本地控制器",
	"random": "随机",
	"search": "两步搜索",
}
const STRATEGY_LABELS := {"balanced": "均衡", "survival": "求生", "dig": "挖洞", "flat": "平整", "clear": "消行"}

var net: Node
var board: Control
var piece_buttons: Dictionary = {}
var ui: Dictionary = {}
var agents: Array = []
var challenges: Array = []
var state: Dictionary = {}
var session: Dictionary = {}
var queue: Array = []
var busy := false
var awaiting := false
var dev_mode := false
var show_pressure := true
var llm_info: Dictionary = {}
var got_hello := false

var anim_path: Array = []
var anim_idx := 0
var anim_t := 0.0
var anim_step := 0.03
var anim_decision: Dictionary = {}
var lock_wait := -1.0
var pending_full: Array = []
var thinking := false
var think_t := 0.0

var replay_frames: Array = []
var replay_idx := 0
var replay_t := 0.0
var replaying := false

var autoplay_left := 0
var screenshot_path := ""
var quit_after_shot := false
var shot_timer := -1.0
var arg_agent := ""
var arg_mode := ""
var rng := RandomNumberGenerator.new()


func _ready() -> void:
	rng.seed = 12345
	_parse_args()
	_build_ui()
	net = NetClient.new()
	add_child(net)
	net.message_received.connect(_on_message)
	net.connection_changed.connect(_on_connection)
	_set_status("正在连接 Agent 服务…")


func _parse_args() -> void:
	for a in OS.get_cmdline_user_args():
		if a.begins_with("--autoplay="):
			autoplay_left = int(a.get_slice("=", 1))
		elif a.begins_with("--screenshot="):
			screenshot_path = a.get_slice("=", 1)
		elif a == "--quit":
			quit_after_shot = true
		elif a == "--dev":
			dev_mode = true
		elif a.begins_with("--agent="):
			arg_agent = a.get_slice("=", 1)
		elif a.begins_with("--mode="):
			arg_mode = a.get_slice("=", 1)


# ------------------------------------------------------------------ UI build
func _style(bg: Color, radius: int = 10, pad: int = 12) -> StyleBoxFlat:
	var s := StyleBoxFlat.new()
	s.bg_color = bg
	s.set_corner_radius_all(radius)
	s.set_content_margin_all(pad)
	return s


func _panel(parent: Control, min_w: float) -> VBoxContainer:
	var p := PanelContainer.new()
	p.add_theme_stylebox_override("panel", _style(Color("#171c29")))
	p.custom_minimum_size = Vector2(min_w, 0)
	parent.add_child(p)
	var sc := ScrollContainer.new()
	sc.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	p.add_child(sc)
	var v := VBoxContainer.new()
	v.add_theme_constant_override("separation", 8)
	v.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	v.size_flags_vertical = Control.SIZE_EXPAND_FILL
	sc.add_child(v)
	return v


func _label(parent: Control, text: String, size: int = 15, col: Color = Color("#d7dceb"), wrap: bool = false) -> Label:
	var l := Label.new()
	l.text = text
	l.add_theme_font_size_override("font_size", size)
	l.add_theme_color_override("font_color", col)
	l.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART if wrap else TextServer.AUTOWRAP_OFF
	if wrap:
		l.custom_minimum_size = Vector2(60, 0)
	parent.add_child(l)
	return l


func _section(parent: Control, text: String) -> void:
	var l := _label(parent, text, 13, Color("#7f8aa3"))
	l.uppercase = true


func _build_ui() -> void:
	var th := Theme.new()
	var sf := SystemFont.new()
	sf.font_names = PackedStringArray(["PingFang SC", "Hiragino Sans GB", "Heiti SC", "Microsoft YaHei", "Noto Sans CJK SC", "Arial Unicode MS"])
	th.default_font = sf
	th.default_font_size = 15
	theme = th

	var bg := ColorRect.new()
	bg.color = Color("#0d1018")
	bg.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	add_child(bg)

	var margin := MarginContainer.new()
	margin.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	for side in ["left", "right", "top", "bottom"]:
		margin.add_theme_constant_override("margin_" + side, 14)
	add_child(margin)
	var root_v := VBoxContainer.new()
	root_v.add_theme_constant_override("separation", 10)
	margin.add_child(root_v)
	var main_h := HBoxContainer.new()
	main_h.size_flags_vertical = Control.SIZE_EXPAND_FILL
	main_h.add_theme_constant_override("separation", 14)
	root_v.add_child(main_h)

	# ---- left: attacker
	var left := _panel(main_h, 280)
	_label(left, "Block the Bot", 24, Color.WHITE)
	_label(left, "你是出题者：选择下一个方块，让 AI Top-out。", 13, Color("#9aa3b8"), true)
	_section(left, "出题（快捷键 I O T S Z J L）")
	var grid := GridContainer.new()
	grid.columns = 4
	grid.add_theme_constant_override("h_separation", 6)
	grid.add_theme_constant_override("v_separation", 6)
	left.add_child(grid)
	for p in Pieces.ORDER:
		var b := PieceButton.new()
		b.piece = p
		b.pressed.connect(_submit.bind(p))
		grid.add_child(b)
		piece_buttons[p] = b
	ui["bag"] = _label(left, "", 13, Color("#9aa3b8"), true)
	ui["budget"] = _label(left, "", 15, Color("#ffd54f"), true)
	ui["pressure_cb"] = CheckBox.new()
	ui["pressure_cb"].text = "显示压力提示（AI 一步评估）"
	ui["pressure_cb"].button_pressed = true
	ui["pressure_cb"].focus_mode = Control.FOCUS_NONE
	ui["pressure_cb"].toggled.connect(func(on: bool) -> void:
		show_pressure = on
		_refresh_buttons())
	left.add_child(ui["pressure_cb"])

	_section(left, "对局设置")
	ui["mode"] = OptionButton.new()
	for i in range(MODE_LABELS.size()):
		ui["mode"].add_item(MODE_LABELS[i], i)
	ui["mode"].select(1)
	ui["mode"].item_selected.connect(_on_mode_changed)
	left.add_child(ui["mode"])
	ui["challenge"] = OptionButton.new()
	ui["challenge"].visible = false
	ui["challenge"].item_selected.connect(func(_i: int) -> void: _update_challenge_desc())
	left.add_child(ui["challenge"])
	ui["challenge_desc"] = _label(left, "", 12, Color("#9aa3b8"), true)
	ui["agent"] = OptionButton.new()
	left.add_child(ui["agent"])
	ui["hold"] = CheckBox.new()
	ui["hold"].text = "AI 可以使用 Hold"
	ui["hold"].button_pressed = false
	ui["hold"].focus_mode = Control.FOCUS_NONE
	left.add_child(ui["hold"])
	var row := HBoxContainer.new()
	left.add_child(row)
	ui["new"] = Button.new()
	ui["new"].text = "新对局 (N)"
	ui["new"].size_flags_horizontal = Control.SIZE_EXPAND_FILL
	ui["new"].pressed.connect(_new_game)
	row.add_child(ui["new"])
	ui["replay_btn"] = Button.new()
	ui["replay_btn"].text = "回放上一局"
	ui["replay_btn"].pressed.connect(_request_replay)
	row.add_child(ui["replay_btn"])
	_section(left, "已提交序列")
	ui["sequence"] = _label(left, "—", 14, Color("#c7cede"), true)
	var spacer := Control.new()
	spacer.size_flags_vertical = Control.SIZE_EXPAND_FILL
	left.add_child(spacer)
	ui["conn"] = _label(left, "", 12, Color("#7f8aa3"), true)

	# ---- centre: board
	var center := VBoxContainer.new()
	center.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	center.add_theme_constant_override("separation", 6)
	main_h.add_child(center)
	ui["status"] = _label(center, "", 17, Color.WHITE, true)
	ui["status"].horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	board = BoardView.new()
	board.size_flags_vertical = Control.SIZE_EXPAND_FILL
	board.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	board.custom_minimum_size = Vector2(260, 400)
	center.add_child(board)

	# ---- right: AI panel
	var right := _panel(main_h, 330)
	_section(right, "AI 防守者")
	ui["agent_label"] = _label(right, "—", 18, Color.WHITE)
	ui["ai_state"] = _label(right, "等待出题", 14, Color("#9aa3b8"), true)
	var hold_row := HBoxContainer.new()
	hold_row.add_theme_constant_override("separation", 10)
	right.add_child(hold_row)
	var hv := VBoxContainer.new()
	hold_row.add_child(hv)
	_label(hv, "Hold", 12, Color("#7f8aa3"))
	ui["hold_icon"] = PieceIcon.new()
	ui["hold_icon"].custom_minimum_size = Vector2(76, 50)
	hv.add_child(ui["hold_icon"])
	var stats := GridContainer.new()
	stats.columns = 2
	stats.add_theme_constant_override("h_separation", 14)
	stats.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	hold_row.add_child(stats)
	for k in [["survived", "已生存"], ["lines", "消行"], ["height", "最高"], ["holes", "空洞"]]:
		var box := VBoxContainer.new()
		stats.add_child(box)
		_label(box, k[1], 12, Color("#7f8aa3"))
		ui["stat_" + k[0]] = _label(box, "0", 20, Color.WHITE)
	_section(right, "局面风险")
	ui["risk"] = ProgressBar.new()
	ui["risk"].min_value = 0.0
	ui["risk"].max_value = 1.0
	ui["risk"].step = 0.001
	ui["risk"].show_percentage = false
	ui["risk"].custom_minimum_size = Vector2(0, 14)
	ui["risk"].add_theme_stylebox_override("background", _style(Color("#242b3b"), 6, 0))
	ui["risk_fill"] = _style(Color("#5fd068"), 6, 0)
	ui["risk"].add_theme_stylebox_override("fill", ui["risk_fill"])
	right.add_child(ui["risk"])
	ui["risk_text"] = _label(right, "", 13, Color("#9aa3b8"), true)
	_section(right, "AI 决策")
	ui["decision"] = _label(right, "—", 14, Color("#d7dceb"), true)
	ui["strategy"] = _label(right, "", 14, Color("#b7a6ff"), true)
	_section(right, "对手模型")
	ui["opponent"] = _label(right, "—", 14, Color("#d7dceb"), true)
	ui["llm"] = _label(right, "", 12, Color("#7f8aa3"), true)
	ui["dev_cb"] = CheckButton.new()
	ui["dev_cb"].text = "开发者模式 (F1)"
	ui["dev_cb"].focus_mode = Control.FOCUS_NONE
	ui["dev_cb"].button_pressed = dev_mode
	ui["dev_cb"].toggled.connect(func(on: bool) -> void:
		dev_mode = on
		_refresh_dev())
	right.add_child(ui["dev_cb"])
	ui["dev_box"] = VBoxContainer.new()
	right.add_child(ui["dev_box"])
	var lvl_row := HBoxContainer.new()
	ui["dev_box"].add_child(lvl_row)
	_label(lvl_row, "信息协议", 12, Color("#7f8aa3"))
	ui["level"] = OptionButton.new()
	for item in ["L0 完整", "L1 摘要+全部候选", "L2 Top-8", "L2 Top-4", "L2 Top-16"]:
		ui["level"].add_item(item)
	ui["level"].select(2)
	lvl_row.add_child(ui["level"])
	ui["dev"] = RichTextLabel.new()
	ui["dev"].bbcode_enabled = false
	ui["dev"].scroll_active = false
	ui["dev"].fit_content = true
	ui["dev"].selection_enabled = true
	ui["dev"].custom_minimum_size = Vector2(280, 60)
	ui["dev"].add_theme_font_size_override("normal_font_size", 12)
	ui["dev_box"].add_child(ui["dev"])

	# ---- bottom: timeline
	var tl_panel := PanelContainer.new()
	tl_panel.add_theme_stylebox_override("panel", _style(Color("#171c29"), 10, 8))
	tl_panel.custom_minimum_size = Vector2(0, 70)
	root_v.add_child(tl_panel)
	var tl_scroll := ScrollContainer.new()
	tl_scroll.vertical_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	tl_panel.add_child(tl_scroll)
	ui["tl_scroll"] = tl_scroll
	ui["timeline"] = HBoxContainer.new()
	ui["timeline"].add_theme_constant_override("separation", 5)
	tl_scroll.add_child(ui["timeline"])

	# ---- game over overlay
	var ov := PanelContainer.new()
	ov.add_theme_stylebox_override("panel", _style(Color("#1d2436", 0.97), 14, 22))
	ov.custom_minimum_size = Vector2(420, 0)
	ov.grow_horizontal = Control.GROW_DIRECTION_BOTH
	ov.grow_vertical = Control.GROW_DIRECTION_BOTH
	ov.set_anchors_and_offsets_preset(Control.PRESET_CENTER, Control.PRESET_MODE_MINSIZE)
	ov.visible = false
	add_child(ov)
	ui["overlay"] = ov
	var ovv := VBoxContainer.new()
	ovv.add_theme_constant_override("separation", 12)
	ov.add_child(ovv)
	ui["ov_title"] = _label(ovv, "", 26, Color.WHITE)
	ui["ov_title"].horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	ui["ov_text"] = _label(ovv, "", 15, Color("#d7dceb"), true)
	ui["ov_text"].horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	var ovb := HBoxContainer.new()
	ovb.alignment = BoxContainer.ALIGNMENT_CENTER
	ovv.add_child(ovb)
	var again := Button.new()
	again.text = "再来一局"
	again.pressed.connect(_new_game)
	ovb.add_child(again)
	var rp := Button.new()
	rp.text = "回放这局"
	rp.pressed.connect(_request_replay)
	ovb.add_child(rp)
	var close := Button.new()
	close.text = "查看棋盘"
	close.pressed.connect(func() -> void: ui["overlay"].visible = false)
	ovb.add_child(close)
	_refresh_dev()


# ------------------------------------------------------------- networking
func _on_connection(ok: bool, text: String) -> void:
	ui["conn"].text = text
	if not ok:
		_set_status(text)


func _on_message(msg: Dictionary) -> void:
	queue.append(msg)


func _handle(msg: Dictionary) -> void:
	match String(msg.get("type", "")):
		"hello":
			_on_hello(msg)
		"state":
			_apply_state(msg)
		"thinking":
			thinking = true
			think_t = 0.0
			ui["ai_state"].text = "思考中：为 %s 评估 %d 个可达落点…" % [msg.get("piece", "?"), int(msg.get("candidate_count", 0))]
			_set_status("AI 正在思考…")
		"decision":
			_start_decision(msg)
		"replay":
			_start_replay(msg)
		"error":
			awaiting = false
			_set_status("⚠ " + String(msg.get("message", "")), Color("#ff8a80"))
			if msg.has("state"):
				_apply_state(msg["state"])
		_:
			pass


func _on_hello(msg: Dictionary) -> void:
	got_hello = true
	agents = msg.get("agents", [])
	challenges = msg.get("challenges", [])
	llm_info = msg.get("llm", {})
	ui["agent"].clear()
	var want := arg_agent
	if want == "":
		want = "llm" if bool(llm_info.get("available", false)) else "heuristic"
	for i in range(agents.size()):
		ui["agent"].add_item(String(agents[i]["label"]), i)
		if agents[i]["id"] == want:
			ui["agent"].select(i)
	ui["challenge"].clear()
	for i in range(challenges.size()):
		ui["challenge"].add_item(String(challenges[i]["name"]), i)
	if arg_mode != "":
		var mi := MODES.find(arg_mode)
		if mi >= 0:
			ui["mode"].select(mi)
	_on_mode_changed(ui["mode"].selected)
	_update_llm_label()
	_new_game()


func _update_llm_label() -> void:
	if bool(llm_info.get("available", false)):
		ui["llm"].text = "0.6B 服务：在线（%s）" % String(llm_info.get("served_model", llm_info.get("model", "")))
	else:
		ui["llm"].text = "0.6B 服务：离线 → 模型模式将使用启发式回退\n(%s)" % String(llm_info.get("url", ""))


func _on_mode_changed(idx: int) -> void:
	ui["challenge"].visible = idx == 2
	ui["hold"].disabled = idx == 2
	ui["hold"].button_pressed = idx == 0
	_update_challenge_desc()


func _update_challenge_desc() -> void:
	var idx: int = ui["mode"].selected
	if idx == 2 and challenges.size() > 0:
		var c: Dictionary = challenges[max(ui["challenge"].selected, 0)]
		ui["challenge_desc"].text = String(c.get("description", ""))
	elif idx == 1:
		ui["challenge_desc"].text = "每袋 7 种方块各用一次，由你决定顺序。AI 存活 150 块即获胜。"
	else:
		ui["challenge_desc"].text = "任意出题，无块数上限——用尽量少的块数推倒 AI。"


func _new_game() -> void:
	if not net.connected:
		return
	_stop_replay()
	ui["overlay"].visible = false
	board.clear_all()
	queue.clear()
	busy = false
	awaiting = false
	thinking = false
	anim_path = []
	var mode_idx: int = ui["mode"].selected
	var agent_id := "heuristic"
	if agents.size() > 0:
		agent_id = String(agents[max(ui["agent"].selected, 0)]["id"])
	var lvl: int = ui["level"].selected
	var levels := ["L0", "L1", "L2", "L2", "L2"]
	var ks := [0, 0, 8, 4, 16]
	var msg := {"type": "new_game", "mode": MODES[mode_idx], "agent": agent_id, "hold": ui["hold"].button_pressed,
		"info_level": levels[lvl], "top_k": ks[lvl]}
	if mode_idx == 2 and challenges.size() > 0:
		msg["challenge_id"] = challenges[max(ui["challenge"].selected, 0)]["id"]
	_update_challenge_desc()
	net.send(msg)
	ui["decision"].text = "—"
	ui["strategy"].text = ""
	_set_status("新对局开始")


func _submit(p: String) -> void:
	if busy or awaiting or replaying or state.is_empty():
		return
	if session.get("winner") != null:
		return
	var g: Dictionary = state
	if String(g.get("phase", "")) != "await_piece":
		return
	if not String(g.get("allowed", "")).contains(p):
		_set_status("%s 已在本袋用过" % p, Color("#ff8a80"))
		return
	awaiting = true
	net.send({"type": "submit_piece", "piece": p, "state_version": int(g.get("state_version", 0))})
	_set_status("你出了 %s" % p)


func _request_replay() -> void:
	ui["overlay"].visible = false
	net.send({"type": "get_replay", "which": "last"})


# ------------------------------------------------------------------ state
func _apply_state(msg: Dictionary) -> void:
	awaiting = false
	thinking = false
	state = msg.get("game", {})
	session = msg.get("session", {})
	llm_info = session.get("llm", llm_info)
	board.setup(state.get("ruleset", {}))
	board.reconcile(state.get("rows", []))
	board.ghost_sets = []
	board.banner = ""
	var f: Dictionary = state.get("features", {})
	ui["stat_survived"].text = str(int(state.get("pieces_placed", 0)))
	ui["stat_lines"].text = str(int(state.get("lines_total", 0)))
	ui["stat_height"].text = str(int(f.get("max", 0)))
	ui["stat_holes"].text = str(int(f.get("holes", 0)))
	var risk: float = float(f.get("risk", 0.0))
	ui["risk"].value = risk
	ui["risk_fill"].bg_color = Color("#5fd068").lerp(Color("#ef5350"), clampf(risk * 1.3, 0.0, 1.0))
	ui["risk_text"].text = "风险 %.2f · 顶部危险 %.2f · 起伏 %d · 井深 %d" % [risk, float(f.get("top_risk", 0)), int(f.get("bump", 0)), int(f.get("max_well", 0))]
	ui["hold_icon"].piece = String(state.get("hold", ""))
	ui["agent_label"].text = String(session.get("agent_label", "—"))
	var rs: Dictionary = state.get("ruleset", {})
	var mode_name := "7-Bag" if String(rs.get("mode", "")) == "bag7" else "自由"
	var ch = session.get("challenge")
	if ch is Dictionary:
		mode_name = "挑战：" + String(ch.get("name", ""))
	ui["ai_state"].text = "%s · Hold %s · 等待出题" % [mode_name, "开" if bool(rs.get("hold", false)) else "关"]
	if String(rs.get("mode", "")) == "bag7":
		var rem := String(state.get("bag_remaining", ""))
		var spaced := ""
		for i in range(rem.length()):
			spaced += rem[i] + " "
		ui["bag"].text = "本袋剩余：" + spaced.strip_edges()
	else:
		ui["bag"].text = "自由模式：任意方块"
	var bl = session.get("budget_left")
	if bl != null:
		ui["budget"].text = "出题预算：剩余 %d 块" % int(bl)
	elif session.get("ai_target") != null:
		ui["budget"].text = "AI 目标：存活 %d 块（当前 %d）" % [int(session["ai_target"]), int(state.get("pieces_placed", 0))]
	else:
		ui["budget"].text = "已出 %d 块" % int(state.get("pieces_submitted", 0))
	var opp: Dictionary = msg.get("opponent", {})
	ui["opponent"].text = String(opp.get("tendency", "—"))
	_update_llm_label()
	_refresh_buttons(msg.get("pressure", {}))
	_refresh_timeline(msg.get("timeline", []))
	ui["dev_stats"] = msg.get("dev", {})
	_refresh_dev()
	if session.get("winner") != null:
		_show_result()
	elif String(state.get("phase", "")) == "await_piece":
		_set_status("轮到你出题")
		_autoplay_step(msg.get("pressure", {}))


var last_pressure: Dictionary = {}


func _refresh_buttons(pressure = null) -> void:
	if pressure is Dictionary:
		last_pressure = pressure
	var allowed := String(state.get("allowed", ""))
	var over: bool = session.get("winner") != null
	for p in piece_buttons:
		var b = piece_buttons[p]
		b.disabled = over or not allowed.contains(p)
		b.set_info(last_pressure.get(p), show_pressure)


func _refresh_timeline(tl: Array) -> void:
	var box: HBoxContainer = ui["timeline"]
	for c in box.get_children():
		c.queue_free()
	for e in tl:
		var chip := PanelContainer.new()
		var d: float = float(e.get("delta", 0.0))
		var col := Color("#242b3b")
		if int(e.get("lines", 0)) > 0:
			col = Color("#1f4d36")
		elif d > 0.04:
			col = Color("#5a2630")
		chip.add_theme_stylebox_override("panel", _style(col, 6, 5))
		var v := VBoxContainer.new()
		v.add_theme_constant_override("separation", 0)
		chip.add_child(v)
		var head := "%s" % e.get("piece", "?")
		if bool(e.get("hold_only", false)):
			head += "→H"
		elif bool(e.get("hold", false)):
			head += "⇄" + String(e.get("placed", ""))
		var l1 := _label(v, head, 14, Pieces.color_of(String(e.get("piece", ""))))
		l1.autowrap_mode = TextServer.AUTOWRAP_OFF
		var sub := ("+%d行" % int(e["lines"])) if int(e.get("lines", 0)) > 0 else ("%+.2f" % d)
		var l2 := _label(v, sub, 11, Color("#c7cede"))
		l2.autowrap_mode = TextServer.AUTOWRAP_OFF
		chip.tooltip_text = "#%d %s · 来源 %s" % [int(e.get("n", 0)), String(e.get("piece", "")), SOURCE_LABELS.get(String(e.get("source", "")), "")]
		box.add_child(chip)
	var seq := ""
	for e in tl:
		seq += String(e.get("piece", ""))
	ui["sequence"].text = seq if seq != "" else "—"
	await get_tree().process_frame
	var sc: ScrollContainer = ui["tl_scroll"]
	sc.scroll_horizontal = int(sc.get_h_scroll_bar().max_value)


func _refresh_dev() -> void:
	ui["dev_box"].visible = dev_mode
	if not dev_mode:
		return
	var s: Dictionary = ui.get("dev_stats", {})
	var d: Dictionary = ui.get("last_decision", {})
	var t := ""
	t += "调用 %s 次 / %s 块（每块 %.2f）  回退率 %.0f%%  合法率 %s\n" % [str(s.get("llm_calls", 0)), str(s.get("turns", 0)), float(s.get("calls_per_piece", 0.0)), 100.0 * float(s.get("fallback_rate", 0.0)), str(s.get("legal_rate", "-"))]
	t += "延迟 P50 %s ms · P95 %s ms\n" % [str(s.get("p50_ms", "-")), str(s.get("p95_ms", "-"))]
	t += "平均 Token 入 %s / 出 %s\n" % [str(s.get("avg_tok_in", "-")), str(s.get("avg_tok_out", "-"))]
	t += "来源统计 %s\n" % JSON.stringify(s.get("sources", {}))
	if not d.is_empty():
		t += "\n— 上一步 —\n"
		t += "候选 %d · 来源 %s · 决策 %.1f ms" % [int(d.get("candidate_count", 0)), String(d.get("source", "")), float(d.get("decide_ms", 0))]
		if bool(d.get("llm_called", false)):
			t += " · 推理 %.1f ms · Token %d/%d%s" % [float(d.get("inference_ms", 0)), int(d.get("tokens_in", 0)), int(d.get("tokens_out", 0)), "（估算）" if bool(d.get("tokens_estimated", false)) else ""]
		t += "\n事件 " + ", ".join(PackedStringArray(d.get("events", []))) + "\n"
		if String(d.get("prompt", "")) != "":
			t += "\n[模型输入]\n" + String(d["prompt"]) + "\n"
			t += "\n[模型输出]\n" + String(d.get("raw", "")) + "\n"
	ui["dev"].text = t


# --------------------------------------------------------------- decisions
func _start_decision(d: Dictionary) -> void:
	thinking = false
	ui["last_decision"] = d
	anim_decision = d
	busy = true
	var src := String(d.get("source", ""))
	var txt := "[%s] %s" % [SOURCE_LABELS.get(src, src), String(d.get("reason", ""))]
	if bool(d.get("hold_only", false)):
		txt += "\n→ 把 %s 放进 Hold" % String(d.get("piece", ""))
	elif bool(d.get("hold", false)):
		txt += "\n→ 交换 Hold，放下 %s" % String(d.get("piece", ""))
	if int(d.get("lines", 0)) > 0:
		txt += "\n→ 消除 %d 行%s" % [int(d["lines"]), "（T-Spin!）" if bool(d.get("tspin", false)) else ""]
	ui["decision"].text = txt
	var strat = d.get("strategy")
	if strat is Dictionary and strat.has("strategy"):
		var sname := String(strat["strategy"])
		ui["strategy"].text = "当前策略：%s（%s 制定，有效 %d 块）" % [STRATEGY_LABELS.get(sname, sname), "0.6B" if String(strat.get("source", "")) == "llm" else "规则", int(strat.get("valid_for", 0))]
	else:
		ui["strategy"].text = ""
	var delta: float = float(d.get("pressure_delta", 0.0))
	_set_status("你的出题让风险 %+.2f" % delta, Color("#ff8a80") if delta > 0.02 else Color("#9be7a5"))
	if dev_mode:
		var g: Array = []
		g.append(d.get("cells", []))
		for c in d.get("top_cells", []):
			g.append(c)
		board.ghost_sets = g
	_refresh_dev()
	anim_path = d.get("path", [])
	anim_idx = 0
	anim_t = 0.0
	if anim_path.is_empty():
		_finish_lock()
	else:
		anim_step = minf(0.035, 0.75 / float(anim_path.size()))
		board.active_color = Pieces.color_of(String(d.get("piece", "")))
		board.active_cells = anim_path[0]
		board.queue_redraw()


func _finish_lock() -> void:
	var d := anim_decision
	if bool(d.get("hold_only", false)):
		lock_wait = 0.15
		pending_full = []
		return
	pending_full = board.lock_piece(d.get("cells", []), String(d.get("piece", "")))
	lock_wait = 0.22 if not pending_full.is_empty() else 0.06


func _process(delta: float) -> void:
	if thinking:
		think_t += delta
		var dots := ".".repeat(1 + int(think_t * 3.0) % 3)
		_set_status("AI 正在思考" + dots)
	if replaying:
		_replay_tick(delta)
		return
	if not anim_path.is_empty():
		anim_t += delta
		while anim_t >= anim_step and not anim_path.is_empty():
			anim_t -= anim_step
			anim_idx += 1
			if anim_idx >= anim_path.size():
				anim_path = []
				_finish_lock()
			else:
				board.active_cells = anim_path[anim_idx]
				board.queue_redraw()
		return
	if lock_wait >= 0.0:
		lock_wait -= delta
		if lock_wait < 0.0:
			board.collapse_rows(pending_full)
			pending_full = []
			busy = false
		return
	while not busy and not queue.is_empty():
		_handle(queue.pop_front())
	if shot_timer >= 0.0:
		shot_timer -= delta
		if shot_timer < 0.0:
			_take_screenshot()


func _unhandled_input(event: InputEvent) -> void:
	var ke := event as InputEventKey
	if ke == null or not ke.pressed or ke.echo:
		return
	var k := OS.get_keycode_string(ke.keycode)
	if k.length() == 1 and Pieces.ORDER.contains(k):
		_submit(k)
	elif ke.keycode == KEY_F1:
		ui["dev_cb"].button_pressed = not dev_mode
	elif ke.keycode == KEY_N:
		_new_game()
	elif ke.keycode == KEY_ESCAPE and replaying:
		_stop_replay()


func _set_status(t: String, col: Color = Color.WHITE) -> void:
	ui["status"].text = t
	ui["status"].add_theme_color_override("font_color", col)


func _show_result() -> void:
	var human: bool = session.get("winner") == "human"
	ui["ov_title"].text = "你赢了！" if human else "AI 获胜"
	ui["ov_title"].add_theme_color_override("font_color", Color("#9be7a5") if human else Color("#ff8a80"))
	var t := String(session.get("result_text", ""))
	t += "\n出题 %d 块 · AI 消行 %d" % [int(state.get("pieces_submitted", 0)), int(state.get("lines_total", 0))]
	if session.get("leaderboard_rank") != null:
		t += "\n本地排行榜第 %d 名（块数越少越好）" % int(session["leaderboard_rank"])
	if String(session.get("replay_path", "")) != "":
		t += "\n回放：" + String(session["replay_path"])
	ui["ov_text"].text = t
	ui["overlay"].visible = true
	board.banner = "TOP OUT" if human else "AI SURVIVED"
	board.queue_redraw()
	_set_status(String(session.get("result_text", "")))
	if autoplay_left > 0 or screenshot_path != "":
		autoplay_left = 0
		_schedule_shot()


# ------------------------------------------------------------------ replay
func _start_replay(msg: Dictionary) -> void:
	if not bool(msg.get("ok", false)):
		_set_status("回放校验失败：" + String(msg.get("error", "")), Color("#ff8a80"))
		return
	replay_frames = msg.get("frames", [])
	replay_idx = 0
	replay_t = 0.0
	replaying = true
	board.clear_all()
	board.setup(msg.get("ruleset", {}))
	_set_status("回放 %s（ESC 退出）· 校验哈希 %s" % [String(msg.get("file", "")), String(msg.get("hash", ""))])


func _replay_tick(delta: float) -> void:
	replay_t -= delta
	if replay_t > 0.0:
		return
	if replay_idx >= replay_frames.size():
		_stop_replay()
		return
	var fr: Dictionary = replay_frames[replay_idx]
	board.set_rows_colored(fr.get("rows", []), fr.get("cells", []), String(fr.get("piece", "")))
	ui["decision"].text = "回放 %d / %d：%s%s" % [replay_idx, replay_frames.size() - 1, String(fr.get("piece", "")), (" 消 %d 行" % int(fr["lines"])) if int(fr.get("lines", 0)) > 0 else ""]
	replay_idx += 1
	replay_t = 0.16


func _stop_replay() -> void:
	if not replaying:
		return
	replaying = false
	replay_frames = []
	if not state.is_empty():
		board.clear_all()
		board.reconcile(state.get("rows", []))
	_set_status("回放结束")


# ---------------------------------------------------------------- autoplay
func _autoplay_step(pressure: Dictionary) -> void:
	if autoplay_left <= 0:
		if screenshot_path != "" and shot_timer < 0.0 and got_hello and int(state.get("pieces_submitted", 0)) > 0:
			_schedule_shot()
		return
	autoplay_left -= 1
	var best := -1.0
	var opts: Array = []
	for p in Pieces.ORDER:
		var v = pressure.get(p)
		if v == null:
			continue
		var fv := float(v)
		if fv > best + 1e-6:
			best = fv
			opts = [p]
		elif absf(fv - best) <= 1e-6:
			opts.append(p)
	if opts.is_empty():
		return
	var pick: String = opts[rng.randi_range(0, opts.size() - 1)]
	get_tree().create_timer(0.05).timeout.connect(func() -> void: _submit(pick))


func _schedule_shot() -> void:
	if screenshot_path != "" and shot_timer < 0.0:
		shot_timer = 0.8


func _take_screenshot() -> void:
	var img := get_viewport().get_texture().get_image()
	img.save_png(screenshot_path)
	print("[main] screenshot saved to ", screenshot_path)
	screenshot_path = ""
	if quit_after_shot:
		get_tree().quit()
