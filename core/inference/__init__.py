"""Engine-agnostic inference layer.

`base` holds the types every caller uses; `ollama_shape` translates between the
Ollama wire format the proxy speaks and the OpenAI format vLLM speaks; the
drivers implement one engine each; `client` picks the driver from the serving
plan and owns admission control.
"""
