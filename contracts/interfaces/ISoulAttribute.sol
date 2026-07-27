// SPDX-License-Identifier: MIT
pragma solidity 0.8.20;

// Copied verbatim (pragma bumped, import path made relative) from
// https://github.com/whitebit-exchange/soul-ecosystem-contracts/blob/main/interfaces/ISoulAttribute.sol

import "./ISoulFeature.sol";

/**
 * @title Soul attribute interface
 * One of soul feature implementations.
 * Soul attributes represent changeable soul features (e.g. hold amount, trade volume etc).
 * @dev Each attribute should be a single contract with its own values specification.
 * Values are stored in registry and are mapped by attribute contract address.
 * The chosen value type is bytes20 because:
 * - the value's slot may be extended with any 12 bytes of data (registry will store set at/updated at there);
 * - this size is quite enough for most cases, and it can be easily extended with dictionaries.
 * If you want to store some complex attributes that don't fit into bytes20, you can create values dictionary
 * (a key is a bytes20 accessor (e.g. ripemd160 hash), a value is anything you want to represent).
 * There is no interface for dictionaries because the value type is unpredictable,
 * so dictionary implementation is up to devs.
 */
interface ISoulAttribute is ISoulFeature {
    /**
     * Validation function, used by registry before setting an attribute.
     * @dev It SHOULD check that operator has permissions to set such attribute;
     * it SHOULD check that specified attribute value is valid, note that value may be 32 zero bytes (empty value);
     * it may also check if attribute is settable for a specific soul.
     * It SHOULD revert execution if validation doesn't pass, so attribute would not be set.
     * @param operator address that is going to modify state
     * @param soulId specific soul id
     * @param value requested value
     */
    function assertIsSettable(address operator, uint256 soulId, bytes20 value) external view;
}
