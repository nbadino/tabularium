from app.services.pipeline import list_plugins, register_plugin, PluginDescriptor


def test_builtin_pipeline_plugins_are_discoverable():
    kinds = {item["kind"] for item in list_plugins()}
    assert {"scanner", "prelabeler", "exporter", "trainer", "evaluator", "inference"} <= kinds


def test_plugin_registration_is_replaceable():
    register_plugin(PluginDescriptor("test-plugin", "exporter", "Test exporter", "0.1"))
    assert any(item["plugin_id"] == "test-plugin" for item in list_plugins())
