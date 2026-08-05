// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

/// @title MockRouterKYA
/// @notice TEST SCAFFOLDING ONLY. Implements exactly the interface that
/// AgentPayRouter expects from its soul registry — `soulOf(address)` and
/// `isVerified(uint256)`. The ecosystem MockSoulRegistry uses the attribute
/// pattern and does NOT expose `isVerified(uint256)`, so it cannot drive the
/// router's KYA gate on a passing buyer; this minimal mock does.
/// Not deployed by any production path.
contract MockRouterKYA {
    mapping(address => uint256) public soulOf;
    mapping(uint256 => bool) public isVerified;

    function setSoul(address account, uint256 soulId) external {
        soulOf[account] = soulId;
    }

    function setVerified(uint256 soulId, bool verified) external {
        isVerified[soulId] = verified;
    }
}
