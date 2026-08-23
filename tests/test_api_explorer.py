from onramp.api_explorer import api_explorer_html, build_openapi_document


def test_openapi_document_describes_paths_parameters_and_json_bodies():
    document = build_openapi_document(
        [
            {
                "path": "/api/items/{item_id}",
                "method": "PUT",
                "tag": "Items",
                "summary": "Update an item",
                "description": "Update one item by identifier.",
                "operation_id": "put_items_item_id",
            }
        ]
    )

    operation = document["paths"]["/api/items/{item_id}"]["put"]
    assert document["openapi"] == "3.1.0"
    assert document["tags"] == [{"name": "Items"}]
    assert operation["parameters"] == [
        {
            "name": "item_id",
            "in": "path",
            "required": True,
            "description": "Value for item_id",
            "schema": {"type": "string"},
        }
    ]
    assert operation["requestBody"]["content"]["application/json"]["schema"] == {
        "type": "object",
        "additionalProperties": True,
    }


def test_api_explorer_is_self_contained_and_loads_the_generated_spec():
    html = api_explorer_html()

    assert "Explore your API." in html
    assert 'fetch("/api/openapi.json"' in html
    assert "Send request" in html
    assert "https://" not in html
