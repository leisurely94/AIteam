"""配置解析：provider 默认值、密钥来源、非法配置。"""
import os
import unittest
from unittest import mock

from ai_team.config import ConfigError, load_settings


class GatewayProviderTest(unittest.TestCase):
    def test_gateway_defaults(self):
        env = {"AITEAM_PROVIDER": "gateway", "AITEAM_API_KEY": "sk-test"}
        with mock.patch.dict(os.environ, env, clear=False):
            settings = load_settings()
        self.assertEqual(settings.base_url, "https://agentgateway-aii.datarangers-onpremise.volces.com/v1")
        self.assertEqual(settings.model, "doubao-seed-2.1-lite")
        self.assertEqual(settings.wire_api, "chat")

    def test_gateway_key_fallback(self):
        env = {"AITEAM_PROVIDER": "gateway", "GATEWAY_API_KEY": "sk-gw", "AITEAM_API_KEY": ""}
        with mock.patch.dict(os.environ, env, clear=False):
            settings = load_settings()
        self.assertEqual(settings.api_key, "sk-gw")

    def test_missing_key_raises(self):
        env = {"AITEAM_PROVIDER": "gateway", "AITEAM_API_KEY": "", "GATEWAY_API_KEY": ""}
        with mock.patch.dict(os.environ, env, clear=False):
            with self.assertRaises(ConfigError):
                load_settings()

    def test_unknown_provider_raises(self):
        env = {"AITEAM_PROVIDER": "nope"}
        with mock.patch.dict(os.environ, env, clear=False):
            with self.assertRaises(ConfigError):
                load_settings()

    def test_bad_wire_api_raises(self):
        env = {"AITEAM_PROVIDER": "gateway", "AITEAM_API_KEY": "sk-test", "AITEAM_WIRE_API": "grpc"}
        with mock.patch.dict(os.environ, env, clear=False):
            with self.assertRaises(ConfigError):
                load_settings()

    def test_gateway_default_timeout(self):
        env = {"AITEAM_PROVIDER": "gateway", "AITEAM_API_KEY": "sk-test", "AITEAM_TIMEOUT": ""}
        with mock.patch.dict(os.environ, env, clear=False):
            self.assertEqual(load_settings().timeout, 300)

    def test_timeout_env_overrides(self):
        env = {"AITEAM_PROVIDER": "gateway", "AITEAM_API_KEY": "sk-test", "AITEAM_TIMEOUT": "90"}
        with mock.patch.dict(os.environ, env, clear=False):
            self.assertEqual(load_settings().timeout, 90)


if __name__ == "__main__":
    unittest.main()
