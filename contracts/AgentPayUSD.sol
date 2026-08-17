// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import {EIP712} from "@openzeppelin/contracts/utils/cryptography/EIP712.sol";
import {ECDSA} from "@openzeppelin/contracts/utils/cryptography/ECDSA.sol";
import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";

/**
 * @title AgentPayUSD
 * @notice Тестовий ERC-20 токен з підтримкою EIP-3009 (transferWithAuthorization).
 *         Це те, що потрібно для стандарт-сумісної схеми x402 "exact":
 *         платник ПІДПИСУЄ дозвіл на переказ (off-chain), а facilitator
 *         відправляє цей підпис у блокчейн і виконує переказ.
 *         Токен використовується ЛИШЕ в testnet для демо AgentPay.
 */
contract AgentPayUSD is ERC20, EIP712, Ownable {
    // keccak256("TransferWithAuthorization(address from,address to,uint256 value,uint256 validAfter,uint256 validBefore,bytes32 nonce)")
    bytes32 public constant TRANSFER_WITH_AUTHORIZATION_TYPEHASH =
        0x7c7c6cdb67a18743f49ec6fa9b35f50d52ed05cbed4cc592e13b44501c1a2267;
    // keccak256("ReceiveWithAuthorization(address from,address to,uint256 value,uint256 validAfter,uint256 validBefore,bytes32 nonce)")
    bytes32 public constant RECEIVE_WITH_AUTHORIZATION_TYPEHASH =
        0xd099cc98ef71107a616c4f0f941f04c322d8e254fe26b3c6668db87aae413de8;
    // keccak256("CancelAuthorization(address authorizer,bytes32 nonce)")
    bytes32 public constant CANCEL_AUTHORIZATION_TYPEHASH =
        0x158b0a9edf7a828aad02f63cd515c68ef2f50ba807396f6d12842833a1597429;

    mapping(address => mapping(bytes32 => bool)) private _authorizationStates;

    event AuthorizationUsed(address indexed authorizer, bytes32 indexed nonce);
    event AuthorizationCanceled(address indexed authorizer, bytes32 indexed nonce);

    uint8 private immutable _decimalsValue;

    constructor(uint8 decimals_, uint256 initialSupply, address initialOwner)
        ERC20("AgentPay USD", "apUSD")
        EIP712("AgentPay USD", "1")
        Ownable(initialOwner)
    {
        _decimalsValue = decimals_;
        _mint(initialOwner, initialSupply);
    }

    function decimals() public view override returns (uint8) {
        return _decimalsValue;
    }

    /// @notice Дозволяє власнику докарбувати токени в testnet (для демо/faucet).
    function mint(address to, uint256 amount) external onlyOwner {
        _mint(to, amount);
    }

    /// @notice Чи вже використаний (або скасований) конкретний nonce авторизації.
    function authorizationState(address authorizer, bytes32 nonce) external view returns (bool) {
        return _authorizationStates[authorizer][nonce];
    }

    function transferWithAuthorization(
        address from, address to, uint256 value,
        uint256 validAfter, uint256 validBefore, bytes32 nonce,
        uint8 v, bytes32 r, bytes32 s
    ) external {
        _requireValidAuthorization(from, nonce, validAfter, validBefore);
        bytes32 structHash = keccak256(abi.encode(
            TRANSFER_WITH_AUTHORIZATION_TYPEHASH,
            from, to, value, validAfter, validBefore, nonce
        ));
        _requireValidSignature(from, structHash, v, r, s);
        _markAuthorizationAsUsed(from, nonce);
        _transfer(from, to, value);
    }

    function receiveWithAuthorization(
        address from, address to, uint256 value,
        uint256 validAfter, uint256 validBefore, bytes32 nonce,
        uint8 v, bytes32 r, bytes32 s
    ) external {
        require(to == msg.sender, "apUSD: caller must be the payee");
        _requireValidAuthorization(from, nonce, validAfter, validBefore);
        bytes32 structHash = keccak256(abi.encode(
            RECEIVE_WITH_AUTHORIZATION_TYPEHASH,
            from, to, value, validAfter, validBefore, nonce
        ));
        _requireValidSignature(from, structHash, v, r, s);
        _markAuthorizationAsUsed(from, nonce);
        _transfer(from, to, value);
    }

    function cancelAuthorization(
        address authorizer, bytes32 nonce, uint8 v, bytes32 r, bytes32 s
    ) external {
        require(!_authorizationStates[authorizer][nonce], "apUSD: authorization used/canceled");
        bytes32 structHash = keccak256(abi.encode(CANCEL_AUTHORIZATION_TYPEHASH, authorizer, nonce));
        _requireValidSignature(authorizer, structHash, v, r, s);
        _authorizationStates[authorizer][nonce] = true;
        emit AuthorizationCanceled(authorizer, nonce);
    }

    function _requireValidAuthorization(
        address authorizer, bytes32 nonce, uint256 validAfter, uint256 validBefore
    ) private view {
        require(block.timestamp > validAfter, "apUSD: authorization not yet valid");
        require(block.timestamp < validBefore, "apUSD: authorization expired");
        require(!_authorizationStates[authorizer][nonce], "apUSD: authorization used/canceled");
    }

    function _requireValidSignature(
        address signer, bytes32 structHash, uint8 v, bytes32 r, bytes32 s
    ) private view {
        bytes32 digest = _hashTypedDataV4(structHash);
        address recovered = ECDSA.recover(digest, v, r, s);
        require(recovered == signer, "apUSD: invalid signature");
    }

    function _markAuthorizationAsUsed(address authorizer, bytes32 nonce) private {
        _authorizationStates[authorizer][nonce] = true;
        emit AuthorizationUsed(authorizer, nonce);
    }
}
