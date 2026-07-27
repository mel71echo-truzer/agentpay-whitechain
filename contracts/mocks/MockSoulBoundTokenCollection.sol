// SPDX-License-Identifier: MIT
pragma solidity 0.8.20;

import "../interfaces/ISoulBoundTokenCollection.sol";
import "../interfaces/ISoulFeature.sol";

/// @title Minimal ISoulBoundTokenCollection stub — a small badge collection
/// that MockSoulRegistry binds to souls via issueSBT(). Stands in for a real
/// SBT collection deployed on Whitechain (e.g. the mainnet EarlyBird SBT
/// collection, 0x57e0Dd3c3128CE9C580196Dc22F6204fc9A0bF18 — see README
/// "Step 0").
contract MockSoulBoundTokenCollection is ISoulBoundTokenCollection {
    uint96 public constant MAX_TOKEN_ID = 99;

    function name() external pure returns (string memory) {
        return "AgentPay Reputation Badges";
    }

    function description() external pure returns (string memory) {
        return "Demo soul-bound reputation badges for the AgentPay PoC";
    }

    function tokenIdsCount() external pure returns (uint96) {
        return MAX_TOKEN_ID + 1;
    }

    function tokenIdAtIndex(uint96 index) external pure returns (uint96) {
        require(index <= MAX_TOKEN_ID, "MockSBT: index out of range");
        return index;
    }

    function tokenURI(uint96 tokenId) external pure returns (string memory) {
        require(tokenId <= MAX_TOKEN_ID, "MockSBT: invalid token id");
        return "ipfs://mock-agentpay-badge";
    }

    /// @dev Mock validation: any token id in range can be bound by anyone
    /// (the registry itself gates who may call issueSBT).
    function assertIsBindable(address, uint256, uint96 tokenId) external pure {
        require(tokenId <= MAX_TOKEN_ID, "MockSBT: invalid token id");
    }

    function supportsInterface(bytes4 interfaceId) external pure returns (bool) {
        return interfaceId == type(ISoulBoundTokenCollection).interfaceId
            || interfaceId == type(ISoulFeature).interfaceId;
    }
}
