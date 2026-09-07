from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
import json
import os
from dataclasses import field
from pathlib import Path
from types import MappingProxyType

from pydantic import SecretStr


@dataclass(frozen=True)
class WechatAccount:
    id: str
    appid: str
    api_key_env: str
    api_key: SecretStr = field(repr=False)
    cover_image: Path

    def __post_init__(self) -> None:
        secret = self.api_key
        if isinstance(secret, SecretStr):
            if not secret.get_secret_value().strip():
                raise ValueError("api_key must not be blank")
            return
        if not isinstance(secret, str):
            raise TypeError("api_key must be str or SecretStr")
        normalized = secret.strip()
        if not normalized:
            raise ValueError("api_key must not be blank")
        object.__setattr__(self, "api_key", SecretStr(normalized))

    def to_dict(self) -> dict[str, str]:
        return {
            "id": self.id,
            "appid": self.appid,
            "api_key_env": self.api_key_env,
            "cover_image": str(self.cover_image),
        }


class _FrozenAccounts(Mapping[str, WechatAccount]):
    """A read-only account index that is also safe for generic serializers."""

    def __init__(self, accounts: Mapping[str, WechatAccount]) -> None:
        self._accounts = MappingProxyType(dict(accounts))

    def __getitem__(self, account_id: str) -> WechatAccount:
        return self._accounts[account_id]

    def __iter__(self) -> Iterator[str]:
        return iter(self._accounts)

    def __len__(self) -> int:
        return len(self._accounts)

    def __repr__(self) -> str:
        return repr(self._accounts)

    def __deepcopy__(self, memo: dict[int, object]) -> _FrozenAccounts:
        cloned = _FrozenAccounts(
            {
                account_id: WechatAccount(
                    id=account.id,
                    appid=account.appid,
                    api_key_env=account.api_key_env,
                    api_key=SecretStr(account.api_key.get_secret_value()),
                    cover_image=account.cover_image,
                )
                for account_id, account in self._accounts.items()
            }
        )
        memo[id(self)] = cloned
        return cloned


@dataclass(frozen=True)
class WechatMatrix:
    accounts: Mapping[str, WechatAccount]

    def __post_init__(self) -> None:
        if not isinstance(self.accounts, _FrozenAccounts):
            object.__setattr__(self, "accounts", _FrozenAccounts(self.accounts))

    def get_account(self, account_id: str) -> WechatAccount:
        return self.accounts[account_id]

    def to_dict(self) -> dict[str, dict[str, dict[str, str]]]:
        return {
            "accounts": {
                account_id: account.to_dict()
                for account_id, account in self.accounts.items()
            }
        }


def load_matrix(
    path: Path,
    *,
    require_cover_exists: bool = True,
    environ: Mapping[str, str] | None = None,
) -> WechatMatrix:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("wechat matrix must be a top-level object")
    raw_accounts = payload.get("accounts")
    if not isinstance(raw_accounts, list) or not raw_accounts:
        raise ValueError("wechat matrix missing accounts list")

    accounts: dict[str, WechatAccount] = {}
    appids: set[str] = set()
    ids: set[str] = set()

    for item in raw_accounts:
        if not isinstance(item, dict):
            raise ValueError("account entry must be object")
        account_id = _required_string(item, "id")
        appid = _required_string(item, "wechatAppid")
        api_key_env = _required_string(item, "apiKeyEnv")
        cover = _required_string(item, "coverImage")
        if account_id in ids:
            raise ValueError("duplicate wechat account id")
        if appid in appids:
            raise ValueError("duplicate wechatAppid")
        ids.add(account_id)
        appids.add(appid)

        environment = environ if environ is not None else os.environ
        api_key = environment.get(api_key_env)
        if api_key is None or not api_key.strip():
            raise ValueError(f"missing env var {api_key_env}")

        cover_path = Path(cover)
        if not cover_path.is_absolute():
            cover_path = (path.parent / cover_path).resolve()
        if require_cover_exists and not cover_path.is_file():
            raise ValueError(f"missing cover image: {cover_path}")
        accounts[account_id] = WechatAccount(
            id=account_id,
            appid=appid,
            api_key_env=api_key_env,
            api_key=SecretStr(api_key.strip()),
            cover_image=cover_path,
        )

    return WechatMatrix(accounts=accounts)


def _required_string(item: dict[object, object], field_name: str) -> str:
    value = item.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()
