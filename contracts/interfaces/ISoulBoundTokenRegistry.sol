// SPDX-License-Identifier: MIT
pragma solidity 0.8.20;

// Copied verbatim (pragma bumped, import paths made relative) from
// https://github.com/whitebit-exchange/soul-ecosystem-contracts/blob/main/interfaces/ISoulBoundTokenRegistry.sol

import "./ISoulBoundTokenCollection.sol";
import "./ISoulFeatureRegistry.sol";

/**
 * @title Soul bound token registry
 * This registry contains all tokens that are bound to souls.
 * Acceptable token collections are stored as features.
 * Registry interface contains methods for requesting bound tokens state and iterating over tokens/souls.
 */
interface ISoulBoundTokenRegistry is ISoulFeatureRegistry {
    /**
     * Check if specified token is bound to a specified soul.
     * @param token generalized token id
     * @param soulId requested soul id
     * @return indicator if token is bound
     */
    function isBound(bytes32 token, uint256 soulId) external view returns (bool);

    /**
     * Check if specified token is bound to a specified soul (alternative).
     * @param collection soul bound tokens collection
     * @param tokenId collection token id
     * @param soulId requested soul id
     * @return indicator if token is bound
     */
    function isBound(ISoulBoundTokenCollection collection, uint96 tokenId, uint256 soulId)
        external
        view
        returns (bool);

    /**
     * Get count of souls that have a specified soul bound token, can be used for iterating over souls.
     * @param token generalized token id
     * @return count souls count
     */
    function soulsCountByToken(bytes32 token) external view returns (uint256 count);

    /**
     * Get soul id that have a specified soul bound token at specific index, can be used for iterating over souls.
     * @param token generalized token id
     * @param index specific index
     * @return soulId soul id at requested index
     */
    function soulByTokenAtIndex(bytes32 token, uint256 index) external view returns (uint256 soulId);

    /**
     * Get count of tokens that are bound to the specified soul, can be used for iterating over soul bound tokens.
     * @param soulId requested soul id
     * @return count tokens count
     */
    function tokensCountBySoul(uint256 soulId) external view returns (uint256 count);

    /**
     * Get token that is bound to the specified soul at specific index, can be used for iterating over tokens.
     * @param soulId requested soul id
     * @param index specific index
     * @return token generalized token id
     */
    function tokenBySoulAtIndex(uint256 soulId, uint256 index) external view returns (bytes32 token);
}
