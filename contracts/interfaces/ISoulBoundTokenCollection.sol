// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

// Copied verbatim (pragma bumped, import path made relative) from
// https://github.com/whitebit-exchange/soul-ecosystem-contracts/blob/main/interfaces/ISoulBoundTokenCollection.sol

import "./ISoulFeature.sol";

/**
 * @title Soul bound token collection interface
 * One of soul feature implementations.
 * Soul bound token is referred to a something that is attached to a soul forever.
 * Soul bound token can represent achievements, reputation, access rights to something etc.
 * Collection represents a list of available token ids and a set of rules for binding them.
 * A single soul bound token is a unique identifier
 * that is derived from collection address and token id from this collection.
 * So different collections may provide same token ids without worrying about clash.
 * Soul bound token id consists of 32 bytes, where 20 bytes is a collection address and 12 bytes is collection token id.
 * This is the reason of limiting token ids count/value size to 2^96 (12 bytes).
 */
interface ISoulBoundTokenCollection is ISoulFeature {
    /**
     * Token ids count in collection, can be used for enumerating provided token ids.
     * @return token ids count
     */
    function tokenIdsCount() external view returns (uint96);

    /**
     * Token id at specific index, can be used for enumerating provided token ids.
     * @return token id at specified index
     */
    function tokenIdAtIndex(uint96 index) external view returns (uint96);

    /**
     * Metadata URI of token with specified id, can be used for attaching some metadata URI.
     * @return metadata URI of token with specified id
     */
    function tokenURI(uint96 tokenId) external view returns (string memory);

    /**
     * Validation function, used by registry before binding a token.
     * @dev It SHOULD check that operator has permissions to bind such token;
     * it SHOULD check that token id is valid, it also may check if token id can be bound to a specific soul.
     * It SHOULD revert execution if validation doesn't pass, so token would not be bound.
     * @param operator address that is going to modify state
     * @param soulId specific soul id
     * @param tokenId specific token id
     */
    function assertIsBindable(address operator, uint256 soulId, uint96 tokenId) external view;
}
