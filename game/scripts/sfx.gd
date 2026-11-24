## Procedural sound effects (synthesised at startup — no audio assets needed).
extends Node

const RATE := 22050

var enabled := true
var _players: Array = []
var _cache: Dictionary = {}


func _ready() -> void:
	for i in range(6):
		var p := AudioStreamPlayer.new()
		add_child(p)
		_players.append(p)
	_cache["pick"] = _synth([660.0, 880.0], 0.07, "square", 0.18)
	_cache["lock"] = _synth([140.0, 90.0], 0.09, "sine", 0.45, 0.0, 0.002)
	_cache["clear1"] = _synth([523.0, 659.0, 784.0], 0.22, "triangle", 0.32)
	_cache["clear2"] = _synth([587.0, 740.0, 880.0], 0.24, "triangle", 0.34)
	_cache["clear3"] = _synth([659.0, 831.0, 988.0, 1175.0], 0.28, "triangle", 0.36)
	_cache["clear4"] = _synth([784.0, 988.0, 1175.0, 1568.0], 0.36, "triangle", 0.38)
	_cache["tspin"] = _synth([440.0, 554.0, 659.0, 880.0, 1109.0], 0.34, "square", 0.22)
	_cache["hold"] = _synth([392.0, 523.0], 0.08, "sine", 0.25)
	_cache["danger"] = _synth([220.0, 196.0, 220.0, 196.0], 0.36, "square", 0.16)
	_cache["win"] = _synth([523.0, 659.0, 784.0, 1047.0, 1319.0], 0.7, "triangle", 0.36)
	_cache["lose"] = _synth([392.0, 330.0, 262.0, 196.0], 0.7, "triangle", 0.32)
	_cache["topout"] = _synth([180.0, 120.0, 80.0], 0.5, "noise", 0.34, -0.6)
	_cache["error"] = _synth([200.0], 0.09, "square", 0.15)


func play(name: String) -> void:
	if not enabled or not _cache.has(name):
		return
	for p in _players:
		if not p.playing:
			p.stream = _cache[name]
			p.play()
			return
	_players[0].stream = _cache[name]
	_players[0].play()


## freqs: notes played in sequence across the duration; kind: sine | square | triangle | noise
func _synth(freqs: Array, dur: float, kind: String, vol: float, sweep: float = 0.0, attack: float = 0.004) -> AudioStreamWAV:
	var n := int(RATE * dur)
	var data := PackedByteArray()
	data.resize(n * 2)
	var phase := 0.0
	var rng := RandomNumberGenerator.new()
	rng.seed = 7
	var seg := float(n) / float(freqs.size())
	for i in range(n):
		var t := float(i) / RATE
		var k := mini(int(float(i) / seg), freqs.size() - 1)
		var local := float(i) - seg * k
		var f: float = float(freqs[k]) * (1.0 + sweep * t)
		phase = fmod(phase + f / RATE, 1.0)
		var s := 0.0
		match kind:
			"square":
				s = 1.0 if phase < 0.5 else -1.0
			"triangle":
				s = 4.0 * absf(phase - 0.5) - 1.0
			"noise":
				s = rng.randf_range(-1.0, 1.0) * 0.7 + sin(TAU * phase) * 0.3
			_:
				s = sin(TAU * phase)
		# per-note envelope: quick attack, exponential-ish decay; whole-sound fade-out
		var env := minf(1.0, local / (attack * RATE)) * exp(-3.0 * local / seg) * (1.0 - float(i) / n * 0.5)
		var v := int(clampf(s * env * vol, -1.0, 1.0) * 32767.0)
		data.encode_s16(i * 2, v)
	var w := AudioStreamWAV.new()
	w.format = AudioStreamWAV.FORMAT_16_BITS
	w.mix_rate = RATE
	w.stereo = false
	w.data = data
	return w
