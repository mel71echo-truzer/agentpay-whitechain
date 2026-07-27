// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

// Copied verbatim (pragma bumped, import paths made relative) from
// https://github.com/whitebit-exchange/soul-ecosystem-contracts/blob/main/interfaces/ISoulAttributeRegistry.sol

import "./ISoulAttribute.sol";
import "./ISoulFeatureRegistry.sol";

/**
 * @title Soul attribute registry interface
 * This registry contains all supported attributes and its values by soul.
 * Acceptable attributes are stored as features.
 * Registry interface contains methods for getting attribute values by soul and iterating over souls/attributes.
 */
interface ISoulAttributeRegistry is ISoulFeatureRegistry {
    /**
     * Get value of specified attribute for specified soul
     * @param soulId requested soul id
     * @param attribute requested attribute address
     * @return value attribute value, 20 zero bytes means value is not set
     */
    function soulAttributeValue(uint256 soulId, ISoulAttribute attribute) external view returns (bytes20 value);

    /**
     * Get timestamp of initial attribute setting for specified soul
     * @param soulId requested soul id
     * @param attribute requested attribute address
     * @return timestamp of initial setting, 0 means attribute is not set
     */
    function soulAttributeSetAt(uint256 soulId, ISoulAttribute attribute) external view returns (uint256);

    /**
     * Get timestamp of attribute change for specified soul
     * @param soulId requested soul id
     * @param attribute requested attribute address
     * @return timestamp of update, 0 means attribute is not set
     */
    function soulAttributeUpdatedAt(uint256 soulId, ISoulAttribute attribute) external view returns (uint256);

    /**
     * Get count of attributes that are set for a requested soul, can be used for iterating over soul attributes
     * @param soulId requested soul id
     * @return count attributes count
     */
    function attributesCountBySoul(uint256 soulId) external view returns (uint256 count);

    /**
     * Get attribute that is set for a requested soul at specific index, can be used for iterating over soul attributes
     * @param soulId requested soul id
     * @param index specific index
     * @return attribute attribute address
     */
    function attributeBySoulAtIndex(uint256 soulId, uint256 index) external view returns (ISoulAttribute attribute);

    /**
     * Get count of souls with requested attribute, can be used for iterating over souls
     * @param attribute requested attribute
     * @return count souls count
     */
    function soulsCountByAttribute(ISoulAttribute attribute) external view returns (uint256 count);

    /**
     * Get soul id with requested attribute at specific index, can be used for iterating over souls
     * @param attribute requested attribute
     * @param index specific index
     * @return soulId soul id
     */
    function soulByAttributeAtIndex(ISoulAttribute attribute, uint256 index) external view returns (uint256 soulId);

    /**
     * Aggregation of soulByAttributeAtIndex and soulAttributeValue
     * @param attribute requested attribute
     * @param index soul index in attribute bindings
     * @return soulId soul id at specified position
     * @return value attribute value at specified position
     */
    function soulAndValueByAttributeAtIndex(ISoulAttribute attribute, uint256 index)
        external
        view
        returns (uint256 soulId, bytes20 value);
}
