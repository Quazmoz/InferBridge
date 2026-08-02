import ssl

from app import errors


def test_is_tls_certificate_error_detects_ssl_error():
    exc = ssl.SSLCertVerificationError("CERTIFICATE_VERIFY_FAILED")
    assert errors.is_tls_certificate_error(exc)


def test_is_tls_certificate_error_walks_cause_chain():
    root = ssl.SSLCertVerificationError("unable to get local issuer certificate")
    wrapper = RuntimeError("download failed")
    wrapper.__cause__ = root
    assert errors.is_tls_certificate_error(wrapper)


def test_is_tls_certificate_error_matches_message_text():
    exc = RuntimeError("…CERTIFICATE_VERIFY_FAILED… while contacting host")
    assert errors.is_tls_certificate_error(exc)


def test_is_tls_certificate_error_false_for_plain_error():
    assert not errors.is_tls_certificate_error(ValueError("nope"))


def test_is_tls_certificate_error_handles_cause_cycle():
    a = RuntimeError("a")
    b = RuntimeError("b")
    a.__cause__ = b
    b.__cause__ = a  # cycle must not hang
    assert not errors.is_tls_certificate_error(a)


def test_openvino_tokenizer_runtime_error_matches_packaged_failure_chain():
    root = RuntimeError(
        'Cannot add extension. Cannot find entry point to the extension library. '
        'Cannot load library "openvino_tokenizers.dll": 126'
    )
    wrapper = RuntimeError("model compilation failed")
    wrapper.__cause__ = root

    assert errors.is_openvino_tokenizer_runtime_error(wrapper)


def test_openvino_tokenizer_runtime_error_rejects_unrelated_device_failure():
    assert not errors.is_openvino_tokenizer_runtime_error(
        RuntimeError("OpenVINO GPU plugin could not compile this model")
    )


def test_format_model_load_error_tokenizer_package_message(monkeypatch):
    monkeypatch.setattr(errors.sys, "frozen", True, raising=False)
    exc = RuntimeError('Cannot load library "openvino_tokenizers.dll": 126')

    msg = errors.format_model_load_error(exc)

    assert "Reinstall the latest InferBridge build" in msg
    assert "falling back to CPU will not fix" in msg
    assert "126" not in msg


def test_format_model_convert_error_tokenizer_dependency_message(monkeypatch):
    monkeypatch.setattr(errors.sys, "frozen", False, raising=False)
    exc = RuntimeError("Cannot add extension openvino_tokenizers.dll: WinError 126")

    msg = errors.format_model_convert_error(exc)

    assert "matching versions" in msg
    assert "openvino-tokenizers" in msg


def test_format_model_load_error_tls_message_and_bundle(monkeypatch):
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", r"C:\certs\corp.pem")
    msg = errors.format_model_load_error(ssl.SSLCertVerificationError("CERTIFICATE_VERIFY_FAILED"))
    assert "TLS certificate" in msg
    assert "python-certifi-win32" in msg
    assert r"C:\certs\corp.pem" in msg  # active bundle surfaced


def test_format_model_load_error_passes_through_plain():
    assert errors.format_model_load_error(ValueError("boom")) == "boom"


def test_format_missing_openvino_mentions_install_and_mock():
    msg = errors.format_missing_openvino()
    assert "requirements.txt" in msg
    assert "OV_LLM_MOCK" in msg


def test_format_model_not_converted_with_source_gives_cli_hint():
    msg = errors.format_model_not_converted("My Model", "/models/m", "org/model")
    assert "optimum-cli export openvino" in msg
    assert "org/model" in msg
    assert "/models/m" in msg


def test_format_model_not_converted_without_source():
    msg = errors.format_model_not_converted("My Model", "/models/m", "")
    assert "optimum-cli" not in msg
    assert "/models/m" in msg


def test_format_device_error_npu_and_gpu_hints():
    npu = errors.format_device_error("NPU", ["CPU"])
    assert "NPU driver" in npu
    assert "--device CPU" in npu

    gpu = errors.format_device_error("GPU", ["CPU"])
    assert "GPU driver" in gpu


def test_format_device_error_lists_available_or_none():
    assert "CPU, GPU" in errors.format_device_error("NPU", ["CPU", "GPU"])
    assert "none detected" in errors.format_device_error("NPU", [])
