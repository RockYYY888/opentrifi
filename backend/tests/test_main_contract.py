import importlib.util

import app.main as main


def test_main_does_not_reexport_service_layer() -> None:
	assert "__getattr__" not in vars(main)
	assert importlib.util.find_spec("app.services.core_support") is None
