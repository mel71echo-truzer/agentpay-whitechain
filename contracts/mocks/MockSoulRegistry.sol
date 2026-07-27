// SPDX-License-Identifier: MIT
pragma solidity 0.8.20;

import "@openzeppelin/contracts/access/Ownable.sol";
import "../interfaces/ISoulRegistry.sol";
import "../interfaces/ISoulAttributeRegistry.sol";
import "../interfaces/ISoulAttribute.sol";
import "../interfaces/ISoulBoundTokenRegistry.sol";
import "../interfaces/ISoulBoundTokenCollection.sol";
import "../interfaces/ISoulFeatureRegistry.sol";
import "../interfaces/ISoulFeature.sol";

/**
 * @title MockSoulRegistry — local stand-in for WB Soul (KYA + reputation)
 *
 * Implements the SAME public interfaces as the real Whitechain contracts
 * (ISoulRegistry, ISoulAttributeRegistry, ISoulBoundTokenRegistry — copied
 * verbatim into contracts/interfaces/ from
 * https://github.com/whitebit-exchange/soul-ecosystem-contracts), so that
 * switching from this mock to the real deployment on Whitechain mainnet/
 * testnet is purely a config change (swap addresses in .env) — no code
 * changes in facilitator.py or here.
 *
 * Why a mock at all: this session's network egress policy blocks the whole
 * whitechain.io domain (RPC, docs, explorer), so we could not confirm
 * whether WB Soul is deployed on testnet with public addresses. Only
 * mainnet addresses are published in the soul-ecosystem-contracts README.
 * See README "Step 0" for the full writeup.
 *
 * Owner (the demo operator) manually plays the role of WhiteBIT's KYC/KYA
 * process: registerSoul() creates a soul for an agent's wallet,
 * setVerified() flips its IsVerified attribute, issueSBT() binds a
 * reputation badge. Everything else (soulOf, soulAttributeValue,
 * tokensCountBySoul, ...) is read-only and matches the real ABI exactly.
 */
contract MockSoulRegistry is ISoulRegistry, ISoulAttributeRegistry, ISoulBoundTokenRegistry, Ownable {
    ISoulAttribute public immutable isVerifiedAttribute;
    ISoulBoundTokenCollection public immutable sbtCollection;

    uint256 private _nextSoulId = 1;

    mapping(address => uint256) private _soulIdOf;
    mapping(uint256 => address) private _primaryAddressOf;
    mapping(uint256 => address[]) private _addressesOf;
    mapping(address => bool) private _addressUsed;

    // soulId => attribute => value (bytes20(0) == not set)
    mapping(uint256 => mapping(address => bytes20)) private _attributeValue;
    mapping(uint256 => mapping(address => uint256)) private _attributeSetAt;
    mapping(uint256 => mapping(address => uint256)) private _attributeUpdatedAt;
    // Enumeration helpers (mock-only; the real registry has its own storage layout).
    mapping(uint256 => address[]) private _soulAttributesList;
    mapping(address => uint256[]) private _attributeSoulsList;

    // generalized token id (20 bytes collection + 12 bytes tokenId) => soulId => bound
    mapping(bytes32 => mapping(uint256 => bool)) private _isBound;
    mapping(uint256 => bytes32[]) private _soulTokensList;
    mapping(bytes32 => uint256[]) private _tokenSoulsList;

    event SoulRegistered(address indexed addr, uint256 indexed soulId);
    event VerifiedSet(uint256 indexed soulId, bool verified);
    event SbtIssued(uint256 indexed soulId, uint96 sbtId, bytes32 token);

    constructor(ISoulAttribute isVerifiedAttribute_, ISoulBoundTokenCollection sbtCollection_) Ownable(msg.sender) {
        isVerifiedAttribute = isVerifiedAttribute_;
        sbtCollection = sbtCollection_;
    }

    // ---------------------------------------------------------------
    // Owner-only demo controls (this is the "manual KYC/KYA" surface)
    // ---------------------------------------------------------------

    /// @notice Registers a brand-new soul for `addr` and makes it the primary address.
    function registerSoul(address addr) external onlyOwner returns (uint256 soulId) {
        require(addr != address(0), "MockSoulRegistry: zero address");
        require(_soulIdOf[addr] == 0, "MockSoulRegistry: address already has a soul");

        soulId = _nextSoulId++;
        _soulIdOf[addr] = soulId;
        _primaryAddressOf[soulId] = addr;
        _addressesOf[soulId].push(addr);
        _addressUsed[addr] = true;

        emit SoulRegistered(addr, soulId);
    }

    /// @notice Sets (or clears) the IsVerified attribute for a soul — the KYA gate.
    function setVerified(uint256 soulId, bool verified) external onlyOwner {
        require(_primaryAddressOf[soulId] != address(0), "MockSoulRegistry: unknown soul");
        _setAttribute(soulId, address(isVerifiedAttribute), verified ? bytes20(uint160(1)) : bytes20(0));
        emit VerifiedSet(soulId, verified);
    }

    /// @notice Binds reputation badge `sbtId` (from the built-in mock SBT collection) to a soul.
    function issueSBT(uint256 soulId, uint96 sbtId) external onlyOwner {
        require(_primaryAddressOf[soulId] != address(0), "MockSoulRegistry: unknown soul");
        sbtCollection.assertIsBindable(msg.sender, soulId, sbtId);

        bytes32 token = _generalizedTokenId(sbtCollection, sbtId);
        require(!_isBound[token][soulId], "MockSoulRegistry: already bound");

        _isBound[token][soulId] = true;
        _soulTokensList[soulId].push(token);
        _tokenSoulsList[token].push(soulId);

        emit SbtIssued(soulId, sbtId, token);
    }

    function _setAttribute(uint256 soulId, address attribute, bytes20 value) internal {
        bool wasSet = _attributeSetAt[soulId][attribute] != 0;
        _attributeValue[soulId][attribute] = value;
        _attributeUpdatedAt[soulId][attribute] = block.timestamp;
        if (!wasSet) {
            _attributeSetAt[soulId][attribute] = block.timestamp;
            _soulAttributesList[soulId].push(attribute);
            _attributeSoulsList[attribute].push(soulId);
        }
    }

    function _generalizedTokenId(ISoulBoundTokenCollection collection, uint96 tokenId) internal pure returns (bytes32) {
        return bytes32(bytes20(address(collection))) | bytes32(uint256(tokenId));
    }

    // ---------------------------------------------------------------
    // ISoulRegistry
    // ---------------------------------------------------------------

    function isSoul(uint256 id) external view returns (bool) {
        return id != 0 && id < _nextSoulId;
    }

    function isSoul(address addr) external view returns (bool) {
        return _soulIdOf[addr] != 0;
    }

    function soulOf(address addr) external view returns (uint256) {
        return _soulIdOf[addr];
    }

    function soulOfRevoked(address) external pure returns (uint256) {
        // Revocation is out of scope for this PoC mock.
        return 0;
    }

    function soulPrimaryAddress(uint256 soulId) external view returns (address) {
        return _primaryAddressOf[soulId];
    }

    function soulAddresses(uint256 soulId) external view returns (address[] memory) {
        return _addressesOf[soulId];
    }

    function isAddressUsed(address addr) external view returns (bool) {
        return _addressUsed[addr];
    }

    // ---------------------------------------------------------------
    // ISoulFeatureRegistry (shared by ISoulAttributeRegistry + ISoulBoundTokenRegistry)
    // ---------------------------------------------------------------

    /// @dev Mock simplification: every feature this registry knows about is
    /// considered permanently active; pausing isn't modeled in this PoC.
    function featureStatus(ISoulFeature) external pure returns (uint8) {
        return 1;
    }

    function featurePausedAt(ISoulFeature) external pure returns (uint256) {
        return 0;
    }

    // ---------------------------------------------------------------
    // ISoulAttributeRegistry
    // ---------------------------------------------------------------

    function soulAttributeValue(uint256 soulId, ISoulAttribute attribute) external view returns (bytes20) {
        return _attributeValue[soulId][address(attribute)];
    }

    function soulAttributeSetAt(uint256 soulId, ISoulAttribute attribute) external view returns (uint256) {
        return _attributeSetAt[soulId][address(attribute)];
    }

    function soulAttributeUpdatedAt(uint256 soulId, ISoulAttribute attribute) external view returns (uint256) {
        return _attributeUpdatedAt[soulId][address(attribute)];
    }

    function attributesCountBySoul(uint256 soulId) external view returns (uint256) {
        return _soulAttributesList[soulId].length;
    }

    function attributeBySoulAtIndex(uint256 soulId, uint256 index) external view returns (ISoulAttribute) {
        return ISoulAttribute(_soulAttributesList[soulId][index]);
    }

    function soulsCountByAttribute(ISoulAttribute attribute) external view returns (uint256) {
        return _attributeSoulsList[address(attribute)].length;
    }

    function soulByAttributeAtIndex(ISoulAttribute attribute, uint256 index) external view returns (uint256) {
        return _attributeSoulsList[address(attribute)][index];
    }

    function soulAndValueByAttributeAtIndex(ISoulAttribute attribute, uint256 index)
        external
        view
        returns (uint256 soulId, bytes20 value)
    {
        soulId = _attributeSoulsList[address(attribute)][index];
        value = _attributeValue[soulId][address(attribute)];
    }

    // ---------------------------------------------------------------
    // ISoulBoundTokenRegistry
    // ---------------------------------------------------------------

    function isBound(bytes32 token, uint256 soulId) public view returns (bool) {
        return _isBound[token][soulId];
    }

    function isBound(ISoulBoundTokenCollection collection, uint96 tokenId, uint256 soulId) external view returns (bool) {
        return isBound(_generalizedTokenId(collection, tokenId), soulId);
    }

    function soulsCountByToken(bytes32 token) external view returns (uint256) {
        return _tokenSoulsList[token].length;
    }

    function soulByTokenAtIndex(bytes32 token, uint256 index) external view returns (uint256) {
        return _tokenSoulsList[token][index];
    }

    function tokensCountBySoul(uint256 soulId) external view returns (uint256) {
        return _soulTokensList[soulId].length;
    }

    function tokenBySoulAtIndex(uint256 soulId, uint256 index) external view returns (bytes32) {
        return _soulTokensList[soulId][index];
    }
}
