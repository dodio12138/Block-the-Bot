## Newline-delimited JSON over TCP to the Python agent service.
## Starts the service automatically (python3 ../agent/server.py) if nothing is listening.
extends Node

signal message_received(msg: Dictionary)
signal connection_changed(ok: bool, text: String)

const HOST := "127.0.0.1"

var port := 47800
var tcp := StreamPeerTCP.new()
var buffer := PackedByteArray()
var connected := false
var spawned_pid := -1
var retry_timer := 0.0
var attempts := 0


func _ready() -> void:
	var env_port := OS.get_environment("TBT_PORT")
	if env_port != "":
		port = int(env_port)
	_try_connect()


func _try_connect() -> void:
	tcp = StreamPeerTCP.new()
	tcp.connect_to_host(HOST, port)
	attempts += 1


func _process(delta: float) -> void:
	tcp.poll()
	var st := tcp.get_status()
	if st == StreamPeerTCP.STATUS_CONNECTED:
		if not connected:
			connected = true
			attempts = 0
			connection_changed.emit(true, "已连接 Agent 服务")
			send({"type": "hello", "protocol_version": 1})
		_read()
	elif st == StreamPeerTCP.STATUS_CONNECTING:
		return
	else:
		if connected:
			connected = false
			buffer.clear()
			connection_changed.emit(false, "与 Agent 服务断开，正在重连…")
		retry_timer -= delta
		if retry_timer <= 0.0:
			if spawned_pid < 0 and attempts >= 1:
				_spawn_server()
			retry_timer = 0.5
			_try_connect()


func _read() -> void:
	var n := tcp.get_available_bytes()
	if n > 0:
		var res: Array = tcp.get_data(n)
		if res[0] == OK:
			buffer.append_array(res[1])
	while true:
		var idx := buffer.find(10)
		if idx < 0:
			break
		var line := buffer.slice(0, idx).get_string_from_utf8()
		buffer = buffer.slice(idx + 1)
		if line.strip_edges() == "":
			continue
		var parsed = JSON.parse_string(line)
		if parsed is Dictionary:
			message_received.emit(parsed)


func send(msg: Dictionary) -> bool:
	if not connected:
		return false
	tcp.put_data((JSON.stringify(msg) + "\n").to_utf8_buffer())
	return true


func _spawn_server() -> void:
	var script_path := (ProjectSettings.globalize_path("res://") + "../agent/server.py").simplify_path()
	if not FileAccess.file_exists(script_path):
		connection_changed.emit(false, "找不到 Agent 服务脚本：" + script_path)
		spawned_pid = 0
		return
	var py := _find_python()
	spawned_pid = OS.create_process(py, PackedStringArray([script_path, "--port", str(port)]))
	print("[net] started agent service pid=%d (%s %s)" % [spawned_pid, py, script_path])
	connection_changed.emit(false, "正在启动 Agent 服务…")


func _find_python() -> String:
	var env := OS.get_environment("TBT_PYTHON")
	if env != "":
		return env
	for p in ["/opt/anaconda3/bin/python3", "/opt/homebrew/bin/python3", "/usr/local/bin/python3", "/usr/bin/python3"]:
		if FileAccess.file_exists(p):
			return p
	return "python3"


func _exit_tree() -> void:
	if spawned_pid > 0:
		OS.kill(spawned_pid)
