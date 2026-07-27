// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

// Copied verbatim (pragma bumped) from
// https://github.com/whitebit-exchange/soul-ecosystem-contracts/blob/main/interfaces/ISoulRegistry.sol

/**
 * @title Soul registry interface
 */
interface ISoulRegistry {
    /**
     * Check if there is a soul with specified id
     * @param id Soul id to query
     * @return Indicator if there is a soul with such id
     */
    function isSoul(uint256 id) external view returns (bool);

    /**
     * Check if specified address is assigned to soul
     * @param addr Address to query
     * @return Indicator if address is assigned to soul
     */
    function isSoul(address addr) external view returns (bool);

    /**
     * Get soul id by specified address
     * @param addr Address to query
     * @return Specified address' soul id (0 means soul does not exist)
     */
    function soulOf(address addr) external view returns (uint256);

    /**
     * Get soul id (initial owner) of revoked address
     * @param addr Address to query
     * @return Revoked address' soul id (0 means address is not revoked)
     */
    function soulOfRevoked(address addr) external view returns (uint256);

    /**
     * Get primary address of a soul with specified id
     * @param soulId Soul id to query
     * @return Soul's primary address (zero address means soul does not exist)
     */
    function soulPrimaryAddress(uint256 soulId) external view returns (address);

    /**
     * Get addresses that are assigned to a soul with specified id
     * @param soulId Soul id to query
     * @return Soul addresses list
     */
    function soulAddresses(uint256 soulId) external view returns (address[] memory);

    /**
     * Check if address is used in registry
     * @param addr Address to check
     * @return whether address is used
     */
    function isAddressUsed(address addr) external view returns (bool);
}
