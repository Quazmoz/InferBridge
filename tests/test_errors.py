import errno
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
        "Cannot add extension. Cannot find entry point to the extension library. "
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


def test_format_model_convert_error_collapses_nested_failure_prefixes():
    exc = RuntimeError(
        "Conversion failed: RuntimeError: Conversion failed: Conversion failed: "
        "Command '['optimum-cli', 'export']' returned non-zero exit status 1."
    )

    msg = errors.format_model_convert_error(exc)

    assert msg == (
        "Conversion failed: Command '['optimum-cli', 'export']' returned non-zero exit status 1."
    )
    assert msg.count("Conversion failed") == 1


def test_format_model_convert_error_collapses_traceback_wrapper():
    exc = RuntimeError(
        "optimum-cli failed\n"
        "Traceback (most recent call last):\n"
        "RuntimeError: Conversion failed: OSError: disk full"
    )

    msg = errors.format_model_convert_error(exc)

    assert "not enough free disk space" in msg
    assert "disk full" not in msg.lower()


def test_format_model_convert_error_reports_disk_exhaustion_from_cause():
    root = OSError(errno.ENOSPC, "No space left on device")
    wrapper = RuntimeError("Optimum export failed")
    wrapper.__cause__ = root

    msg = errors.format_model_convert_error(wrapper)

    assert "not enough free disk space" in msg
    assert "previously working model" in msg


def test_format_model_convert_error_reports_windows_file_lock():
    exc = PermissionError(errno.EACCES, "Access is denied")

    msg = errors.format_model_convert_error(exc)

    assert "Windows is using or blocking" in msg
    assert "antivirus" in msg
    assert "previously working model" in msg


def test_format_model_convert_error_preserves_cross_process_lock_guidance():
    exc = RuntimeError(
        "Another InferBridge process is already preparing this model. Wait for it to finish."
    )

    msg = errors.format_model_convert_error(exc)

    assert "Another InferBridge process" in msg
    assert "close the other InferBridge instance" in msg


def test_format_model_load_error_tls_message_and_bundle(monkeypatch):
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", r"C:\certs\corp.pem")
    msg = errors.format_model_load_error(ssl.SSLCertVerificationError("CERTIFICATE_VERIFY_FAILED"))
    assert "TLS certificate" in msg
    assert "python-certifi-win32" in msg
    assert r"C:\certs\corp.pem" not in msg  # local paths must not be surfaced


def test_format_model_load_error_masks_unclassified_private_details():
    exc = ValueError(r"C:\Users\PrivateUser\Model: prompt=confidential hf_supersecretvalue")
    message = errors.format_model_load_error(exc)
    assert "Model loading failed" in message
    assert "PrivateUser" not in message
    assert "confidential" not in message
    assert "hf_supersecretvalue" not in message


def test_missing_openvino_artifact_still_gets_a_specific_safe_next_step():
    exc = RuntimeError(r"No converted OpenVINO model found at C:\Users\PrivateUser")
    message = errors.format_model_load_error(exc)
    assert "Model Library" in message
    assert "PrivateUser" not in message


def test_npu_level_zero_compiler_error_is_actionable_without_leaking_native_details():
    native = RuntimeError(
        r"Exception from C:\Users\PrivateUser\Models\phi-4: Compilation failed. "
        "Level0 pfnCreate2 result: ZE_RESULT_ERROR_INVALID_ARGUMENT, "
        "token=hf_secret_that_must_never_appear"
    )
    wrapper = RuntimeError("Unable to initialize model")
    wrapper.__cause__ = native

    message = errors.format_model_load_error(wrapper, device="NPU")

    assert "ZE_RESULT_ERROR_INVALID_ARGUMENT" in message
    assert "Intel NPU driver" in message
    assert "has not switched devices" in message
    assert "PrivateUser" not in message
    assert "hf_secret" not in message
    assert "pfnCreate2" not in message


def test_unrelated_or_non_npu_invalid_argument_is_not_misclassified():
    native = RuntimeError("ZE_RESULT_ERROR_INVALID_ARGUMENT in a GPU kernel")
    assert "NPU compilation" not in errors.format_model_load_error(native, device="GPU")
    assert "NPU compilation" not in errors.format_model_load_error(native, device="NPU")


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
