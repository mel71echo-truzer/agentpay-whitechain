// SPDX-License-Identifier: MIT
pragma solidity 0.8.20;

import "../interfaces/ISoulAttribute.sol";
import "../interfaces/ISoulFeature.sol";

/// @title Minimal ISoulAttribute stub used as the "IsVerified" attribute key
/// in MockSoulRegistry. On real Whitechain mainnet this role is played by
/// the deployed IsVerified attribute contract
/// (0xd88fa142B67F561C5f2Cbf803bF5AE906a8f1e41) — see README "Step 0".
contract MockSoulAttribute is ISoulAttribute {
    // ISoulFeature.name()/description() are `pure` in the real interface
    // (plain metadata, usually a public constant in real implementations),
    // so this can't take a constructor-supplied string — deploy one
    // instance of this contract per distinct attribute you need.
    function name() external pure returns (string memory) {
        return "IsVerified";
    }

    function description() external pure returns (string memory) {
        return "Mock KYA verification flag (stands in for WB Soul's IsVerified attribute)";
    }

    /// @dev Mock: only the registry itself is expected to call this via
    /// MockSoulRegistry.setVerified, so validation is a no-op here.
    function assertIsSettable(address, uint256, bytes20) external pure {}

    function supportsInterface(bytes4 interfaceId) external pure returns (bool) {
        return interfaceId == type(ISoulAttribute).interfaceId || interfaceId == type(ISoulFeature).interfaceId;
    }
}
