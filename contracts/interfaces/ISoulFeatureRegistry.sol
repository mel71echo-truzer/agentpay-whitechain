// SPDX-License-Identifier: MIT
pragma solidity 0.8.20;

// Copied verbatim (pragma bumped, import path made relative) from
// https://github.com/whitebit-exchange/soul-ecosystem-contracts/blob/main/interfaces/ISoulFeatureRegistry.sol

import "./ISoulFeature.sol";

/**
 * @title Soul feature registry interface
 * Each feature registry will contain features list,
 * so there should be an interface for getting specific feature's status.
 * Feature statuses are:
 *  - 0 - feature does not exist
 *  - 1 - feature is active
 *  - 2 - feature is paused
 * When a feature is paused, block timestamp is set as a pause time.
 * It can be retrieved by using featurePausedAt() function and can be used for some assertions.
 */
interface ISoulFeatureRegistry {
    /**
     * Get feature status (statuses description is above)
     * @param feature requested feature
     * @return status feature status
     */
    function featureStatus(ISoulFeature feature) external view returns (uint8 status);

    /**
     * Get feature pause timestamp
     * @param feature requested feature
     * @return timestamp pause timestamp (0 if feature is not paused)
     */
    function featurePausedAt(ISoulFeature feature) external view returns (uint256 timestamp);
}
