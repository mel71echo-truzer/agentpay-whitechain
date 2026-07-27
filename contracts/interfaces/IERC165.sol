// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

// Copied verbatim (pragma bumped to match this repo's compiler) from
// https://github.com/whitebit-exchange/soul-ecosystem-contracts/blob/main/interfaces/IERC165.sol

interface IERC165 {
    function supportsInterface(bytes4 interfaceID) external view returns (bool);
}
