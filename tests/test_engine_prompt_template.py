from types import SimpleNamespace

from runtime.openvino_engine import GenParams, OpenVINOEngine


def test_preformatted_prompt_is_not_wrapped_again_for_generate_or_stream():
    class Pipeline:
        def generate(self, prompt, config, streamer=None, **kwargs):
            text = f"wrapped:{prompt}" if config.apply_chat_template else prompt
            if streamer is not None:
                streamer(text)
            return text

    engine = OpenVINOEngine.__new__(OpenVINOEngine)
    engine._closed = False
    engine._pipe = Pipeline()
    engine._ov = SimpleNamespace(
        GenerationConfig=lambda: SimpleNamespace(apply_chat_template=True),
    )
    engine.count_tokens = lambda text: 1
    prompt = "<|user|>hello<|assistant|>"
    assert engine.generate(prompt, GenParams()).text == prompt
    handle = engine.stream(prompt, GenParams())
    assert handle.next_chunk() == prompt
    assert handle.next_chunk() is None
    assert handle.wait_closed()
    assert handle.error is None
