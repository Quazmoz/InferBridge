from runtime import device_check
from runtime.npu_compat import install_openvino_genai_compat

install_openvino_genai_compat()


def test_normalize_device_defaults_and_casing():
    assert device_check.normalize_device(None) == "NPU"
    assert device_check.normalize_device("") == "NPU"
    assert device_check.normalize_device("  npu ") == "NPU"
    assert device_check.normalize_device("gpu") == "GPU"
    assert device_check.normalize_device(" auto:npu, gpu, cpu ") == "AUTO:NPU,GPU,CPU"


def test_parse_device_expression_accepts_simple_and_composite():
    valid = [
        ("CPU", "CPU"),
        ("GPU", "GPU"),
        ("NPU", "NPU"),
        ("AUTO", "AUTO"),
        ("auto", "AUTO"),
        ("AUTO:NPU,GPU,CPU", "AUTO:NPU,GPU,CPU"),
        ("AUTO:GPU,NPU,CPU", "AUTO:GPU,NPU,CPU"),
        ("MULTI:GPU,CPU", "MULTI:GPU,CPU"),
        ("MULTI:NPU,GPU,CPU", "MULTI:NPU,GPU,CPU"),
        ("HETERO:GPU,CPU", "HETERO:GPU,CPU"),
        ("HETERO:NPU,GPU,CPU", "HETERO:NPU,GPU,CPU"),
        ("GPU.0", "GPU.0"),
    ]
    for raw, normalized in valid:
        assert device_check.parse_device_expression(raw).normalized == normalized


def test_parse_device_expression_rejects_invalid_values():
    invalid = [
        "",
        "   ",  # whitespace-only
        "BOGUS",
        "AUTO:",
        "AUTO:BOGUS,CPU",
        "MULTI:NPU,BOGUS",
        "HETERO:",
        "AUTO:NPU,,CPU",
        "AUTO:GPU,GPU,CPU",
        "MULTI:",
        "HETERO:,",
        "AUTO:NPU GPU CPU",  # space-separated, not comma-separated
    ]
    for raw in invalid:
        try:
            device_check.parse_device_expression(raw)
        except device_check.DeviceValidationError:
            pass
        else:
            raise AssertionError(f"{raw!r} should be invalid")


def test_is_device_available_auto_always_true():
    assert device_check.is_device_available("AUTO", [])
    assert device_check.is_device_available("auto", ["CPU"])


def test_is_device_available_matches_plain_and_indexed():
    avail = ["CPU", "GPU.0", "NPU"]
    assert device_check.is_device_available("CPU", avail)
    assert device_check.is_device_available("GPU", avail)  # GPU matches GPU.0
    assert device_check.is_device_available("GPU.0", avail)
    assert not device_check.is_device_available("GPU.1", avail)
    assert device_check.is_device_available("GPU.1", ["CPU", "GPU.1"])
    assert device_check.is_device_available("NPU", avail)
    assert device_check.is_device_available("AUTO:NPU,GPU,CPU", avail)
    assert device_check.is_device_available("MULTI:GPU.0,CPU", avail)
    assert not device_check.is_device_available("MULTI:GPU.1,CPU", avail)


def test_is_device_available_rejects_absent_device():
    assert not device_check.is_device_available("NPU", ["CPU", "GPU"])
    assert not device_check.is_device_available("AUTO:NPU,GPU,CPU", ["CPU", "GPU"])
    assert not device_check.is_device_available("MULTI:NPU,BOGUS", ["CPU", "NPU"])


def test_is_device_available_empty_list_allows_only_cpu():
    assert device_check.is_device_available("CPU", [])
    assert device_check.is_device_available("AUTO", [])
    assert not device_check.is_device_available("GPU", [])
    assert not device_check.is_device_available("AUTO:NPU,GPU,CPU", [])


def test_validate_device_expression_reports_examples():
    try:
        device_check.validate_device_expression("AUTO:BOGUS,CPU", ["CPU"])
    except device_check.DeviceValidationError as exc:
        message = str(exc)
        assert "Detected devices: CPU" in message
        assert "AUTO:NPU,GPU,CPU" in message
    else:
        raise AssertionError("invalid device should raise")


def test_suggested_device_targets_from_available_devices():
    suggestions = device_check.suggested_device_targets(["CPU", "GPU.0", "NPU"])
    devices = [item["device"] for item in suggestions]
    assert "AUTO:NPU,GPU,CPU" in devices
    assert "AUTO:GPU,NPU,CPU" in devices
    assert "MULTI:NPU,GPU,CPU" in devices
    assert any(
        item["device"] == "MULTI:NPU,GPU,CPU" and item["experimental"] for item in suggestions
    )


def test_cached_discovery_results_cannot_be_mutated_by_callers(monkeypatch):
    monkeypatch.setattr(device_check, "_cached_devices", ["CPU", "GPU.0"])
    monkeypatch.setattr(
        device_check,
        "_cached_details",
        [{"device": "CPU", "full_name": "Test CPU"}],
    )

    devices = device_check.available_devices()
    details = device_check.device_details()
    devices.append("NPU")
    details[0]["full_name"] = "mutated"

    assert device_check.available_devices() == ["CPU", "GPU.0"]
    assert device_check.device_details() == [{"device": "CPU", "full_name": "Test CPU"}]


def test_probe_functions_return_safe_types_without_openvino():
    # OpenVINO is absent in this environment; the probes must degrade gracefully.
    assert isinstance(device_check.is_openvino_available(), bool)
    assert isinstance(device_check.available_devices(), list)
    assert isinstance(device_check.device_details(), list)


def test_build_plugin_config(tmp_path):
    from runtime.openvino_engine import build_plugin_config

    # CPU/GPU/AUTO do not receive direct-NPU generation properties.
    cfg_cpu = build_plugin_config("CPU", 1024)
    assert cfg_cpu == {}

    cfg_npu = build_plugin_config("NPU", 1024)
    assert cfg_npu == {"MAX_PROMPT_LEN": 1024, "MIN_RESPONSE_LEN": 128}

    cfg_npu_explicit = build_plugin_config("NPU", 1536, max_response_len=512)
    assert cfg_npu_explicit == {"MAX_PROMPT_LEN": 1536, "MIN_RESPONSE_LEN": 512}

    cfg_indexed_npu = build_plugin_config("NPU.0", 3072, max_response_len=1024)
    assert cfg_indexed_npu == {"MAX_PROMPT_LEN": 3072, "MIN_RESPONSE_LEN": 1024}

    cfg_auto_npu = build_plugin_config("AUTO:NPU,GPU,CPU", 1024)
    assert cfg_auto_npu == {}

    # Cache is common, but embedding pipelines must not receive LLM-only NPU keys.
    cache_dir = tmp_path / "my_cache"
    cfg_cache = build_plugin_config("CPU", 1024, cache_dir=cache_dir)
    assert cfg_cache == {"CACHE_DIR": str(cache_dir)}
    assert cache_dir.exists()

    cfg_npu_cache = build_plugin_config("NPU", 1536, cache_dir=cache_dir, max_response_len=512)
    assert cfg_npu_cache == {
        "MAX_PROMPT_LEN": 1536,
        "MIN_RESPONSE_LEN": 512,
        "CACHE_DIR": str(cache_dir),
    }

    cfg_embedding = build_plugin_config(
        "NPU",
        1024,
        cache_dir=cache_dir,
        max_response_len=512,
        backend="openvino-embeddings",
    )
    assert cfg_embedding == {"CACHE_DIR": str(cache_dir)}
