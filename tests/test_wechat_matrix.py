import json
import copy
from dataclasses import FrozenInstanceError, asdict
from pathlib import Path

import pytest
from pydantic import SecretStr

from windows_capabilities.wechat_matrix import WechatAccount, WechatMatrix, load_matrix


def test_load_matrix_resolves_two_distinct_accounts(tmp_path, monkeypatch) -> None:
    config = tmp_path / "wechat-matrix.json"
    config.write_text(
        json.dumps(
            {
                "accounts": [
                    {
                        "id": "xingzhi",
                        "wechatAppid": "wx-one",
                        "apiKeyEnv": "KEY_ONE",
                        "coverImage": "one.png",
                    },
                    {
                        "id": "qingyun",
                        "wechatAppid": "wx-two",
                        "apiKeyEnv": "KEY_TWO",
                        "coverImage": "two.png",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("KEY_ONE", "secret-one")
    monkeypatch.setenv("KEY_TWO", "secret-two")
    matrix = load_matrix(config, require_cover_exists=False)
    assert matrix.accounts["xingzhi"].appid == "wx-one"
    assert matrix.accounts["qingyun"].api_key.get_secret_value() == "secret-two"
    assert isinstance(matrix.accounts["xingzhi"], WechatAccount)


def test_load_matrix_rejects_duplicate_appids(tmp_path, monkeypatch) -> None:
    config = tmp_path / "wechat-matrix.json"
    config.write_text(
        json.dumps(
            {
                "accounts": [
                    {"id": "a", "wechatAppid": "same", "apiKeyEnv": "A", "coverImage": "a.png"},
                    {"id": "b", "wechatAppid": "same", "apiKeyEnv": "B", "coverImage": "b.png"},
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("A", "one")
    monkeypatch.setenv("B", "two")
    with pytest.raises(ValueError, match="duplicate wechatAppid"):
        load_matrix(config, require_cover_exists=False)


def write_accounts(tmp_path, accounts) -> object:
    config = tmp_path / "wechat-matrix.json"
    config.write_text(json.dumps({"accounts": accounts}), encoding="utf-8")
    return config


def account(account_id="only", appid="wx-one", env="KEY_ONE", cover="covers/one.png"):
    return {"id": account_id, "wechatAppid": appid, "apiKeyEnv": env, "coverImage": cover}


def test_load_matrix_accepts_one_account_and_resolves_relative_cover(tmp_path, monkeypatch) -> None:
    cover = tmp_path / "covers" / "one.png"
    cover.parent.mkdir()
    cover.write_bytes(b"image")
    config = write_accounts(tmp_path, [account()])
    monkeypatch.setenv("KEY_ONE", "secret-one")

    matrix = load_matrix(config)

    assert len(matrix.accounts) == 1
    assert matrix.accounts["only"].cover_image == cover.resolve()


def test_load_matrix_rejects_duplicate_ids(tmp_path, monkeypatch) -> None:
    config = write_accounts(tmp_path, [account("same", "wx-a", "A"), account("same", "wx-b", "B")])
    monkeypatch.setenv("A", "one")
    monkeypatch.setenv("B", "two")
    with pytest.raises(ValueError, match="duplicate wechat account id"):
        load_matrix(config, require_cover_exists=False)


@pytest.mark.parametrize("value", [None, "", "   "])
def test_load_matrix_rejects_missing_or_blank_api_key(tmp_path, monkeypatch, value) -> None:
    config = write_accounts(tmp_path, [account()])
    if value is not None:
        monkeypatch.setenv("KEY_ONE", value)
    else:
        monkeypatch.delenv("KEY_ONE", raising=False)
    with pytest.raises(ValueError, match="missing env var KEY_ONE"):
        load_matrix(config, require_cover_exists=False)


def test_load_matrix_rejects_empty_cover_even_when_not_checking_exists(tmp_path, monkeypatch) -> None:
    config = write_accounts(tmp_path, [account(cover="  ")])
    monkeypatch.setenv("KEY_ONE", "secret")
    with pytest.raises(ValueError, match="coverImage"):
        load_matrix(config, require_cover_exists=False)


def test_load_matrix_rejects_cover_directory(tmp_path, monkeypatch) -> None:
    (tmp_path / "covers").mkdir()
    config = write_accounts(tmp_path, [account(cover="covers")])
    monkeypatch.setenv("KEY_ONE", "secret")
    with pytest.raises(ValueError, match="cover image"):
        load_matrix(config)


def test_account_matrix_are_frozen_and_safe_to_serialize(tmp_path, monkeypatch) -> None:
    config = write_accounts(tmp_path, [account()])
    monkeypatch.setenv("KEY_ONE", "super-secret-value")
    matrix = load_matrix(config, require_cover_exists=False)
    loaded = matrix.accounts["only"]

    with pytest.raises(FrozenInstanceError):
        loaded.appid = "changed"
    with pytest.raises(FrozenInstanceError):
        matrix.accounts = {}

    rendered = repr(loaded) + str(loaded) + repr(matrix) + str(matrix)
    serialized = json.dumps(matrix.to_dict(), ensure_ascii=False)
    generic_account = json.dumps(asdict(loaded), default=str, ensure_ascii=False)
    generic_matrix = json.dumps(asdict(matrix), default=str, ensure_ascii=False)
    assert "super-secret-value" not in rendered
    assert "super-secret-value" not in serialized
    assert "super-secret-value" not in generic_account
    assert "super-secret-value" not in generic_matrix
    assert loaded.api_key.get_secret_value() == "super-secret-value"
    assert matrix.to_dict() == {
        "accounts": {
            "only": {
                "id": "only",
                "appid": "wx-one",
                "api_key_env": "KEY_ONE",
                "cover_image": str((tmp_path / "covers" / "one.png").resolve()),
            }
        }
    }


def test_accounts_mapping_rejects_in_place_set_and_delete(tmp_path, monkeypatch) -> None:
    config = write_accounts(tmp_path, [account()])
    monkeypatch.setenv("KEY_ONE", "secret")
    matrix = load_matrix(config, require_cover_exists=False)

    with pytest.raises(TypeError):
        matrix.accounts["new"] = matrix.accounts["only"]
    with pytest.raises(TypeError):
        del matrix.accounts["only"]
    assert list(matrix.accounts) == ["only"]


def test_load_matrix_rejects_empty_accounts(tmp_path) -> None:
    config = write_accounts(tmp_path, [])
    with pytest.raises(ValueError, match="accounts"):
        load_matrix(config, require_cover_exists=False)


def test_account_direct_construction_normalizes_string_secret() -> None:
    account_obj = WechatAccount("only", "wx-one", "KEY_ONE", " secret ", Path("cover.png"))

    assert isinstance(account_obj.api_key, SecretStr)
    assert account_obj.api_key.get_secret_value() == "secret"


def test_account_direct_construction_preserves_secretstr() -> None:
    secret = SecretStr("secret")

    account_obj = WechatAccount("only", "wx-one", "KEY_ONE", secret, Path("cover.png"))

    assert account_obj.api_key is secret


@pytest.mark.parametrize("secret", ["", "   ", None, 123, [], {}])
def test_account_direct_construction_rejects_invalid_secret(secret) -> None:
    with pytest.raises((TypeError, ValueError), match="api_key"):
        WechatAccount("only", "wx-one", "KEY_ONE", secret, Path("cover.png"))


@pytest.mark.parametrize("secret", [SecretStr(""), SecretStr("   ")])
def test_account_direct_construction_rejects_blank_secretstr(secret) -> None:
    with pytest.raises(ValueError, match="api_key"):
        WechatAccount("only", "wx-one", "KEY_ONE", secret, Path("cover.png"))


@pytest.mark.parametrize("payload", [None, [], "accounts", 1])
def test_load_matrix_rejects_non_object_top_level(tmp_path, payload) -> None:
    config = tmp_path / "wechat-matrix.json"
    config.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="top-level object"):
        load_matrix(config, require_cover_exists=False)


@pytest.mark.parametrize("accounts", [None, {}, "account", 1])
def test_load_matrix_rejects_non_list_accounts(tmp_path, accounts) -> None:
    config = write_accounts(tmp_path, accounts)

    with pytest.raises(ValueError, match="accounts list"):
        load_matrix(config, require_cover_exists=False)


@pytest.mark.parametrize("item", [None, [], "account", 1])
def test_load_matrix_rejects_non_object_account_entry(tmp_path, item) -> None:
    config = write_accounts(tmp_path, [item])

    with pytest.raises(ValueError, match="account entry must be object"):
        load_matrix(config, require_cover_exists=False)


@pytest.mark.parametrize("field", ["id", "wechatAppid", "apiKeyEnv", "coverImage"])
@pytest.mark.parametrize("value", [None, 123, [], {}, "", "   "])
def test_load_matrix_rejects_invalid_account_fields(tmp_path, monkeypatch, field, value) -> None:
    item = account()
    item[field] = value
    config = write_accounts(tmp_path, [item])
    monkeypatch.setenv("KEY_ONE", "secret")

    with pytest.raises(ValueError, match=field):
        load_matrix(config, require_cover_exists=False)


def test_matrix_deepcopy_rebuilds_accounts_and_secret_values(tmp_path, monkeypatch) -> None:
    config = write_accounts(tmp_path, [account()])
    monkeypatch.setenv("KEY_ONE", "secret")
    matrix = load_matrix(config, require_cover_exists=False)

    cloned = copy.deepcopy(matrix)
    original_account = matrix.accounts["only"]
    cloned_account = cloned.accounts["only"]

    assert cloned.accounts is not matrix.accounts
    assert cloned_account is not original_account
    assert cloned_account.api_key is not original_account.api_key
    cloned_account.api_key._secret_value = "changed-for-test"
    assert cloned_account.api_key.get_secret_value() == "changed-for-test"
    assert original_account.api_key.get_secret_value() == "secret"
