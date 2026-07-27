// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

interface IERC20Permit3009 {
    function receiveWithAuthorization(
        address from,
        address to,
        uint256 value,
        uint256 validAfter,
        uint256 validBefore,
        bytes32 nonce,
        uint8 v,
        bytes32 r,
        bytes32 s
    ) external;

    function transfer(address to, uint256 value) external returns (bool);
}

interface ISoulRegistry {
    function soulOf(address account) external view returns (uint256);
    function isVerified(uint256 soulId) external view returns (bool);
}

/**
 * @title AgentPayRouter
 * @notice Atomic Escrow and Fee-Split Router for Agent-to-Agent Payments
 */
contract AgentPayRouter {
    address public immutable teurcToken;
    address public immutable soulRegistry;
    address public owner;
    
    uint256 public facilitatorFeeBps = 50; // 0.5% (50 / 10000)

    event AtomicPaymentSettled(
        address indexed from,
        address indexed seller,
        uint256 totalAmount,
        uint256 sellerAmount,
        uint256 feeAmount,
        bytes32 indexed nonce
    );

    error KYACheckFailed();
    error InvalidFeeConfiguration();
    error Unauthorized();

    modifier onlyOwner() {
        if (msg.sender != owner) revert Unauthorized();
        _;
    }

    constructor(address _teurcToken, address _soulRegistry) {
        teurcToken = _teurcToken;
        soulRegistry = _soulRegistry;
        owner = msg.sender;
    }

    function settlePaymentAtomic(
        address from,
        address seller,
        uint256 amount,
        uint256 validAfter,
        uint256 validBefore,
        bytes32 nonce,
        uint8 v,
        bytes32 r,
        bytes32 s
    ) external {
        uint256 soulId = ISoulRegistry(soulRegistry).soulOf(from);
        if (soulId == 0 || !ISoulRegistry(soulRegistry).isVerified(soulId)) {
            revert KYACheckFailed();
        }

        uint256 feeAmount = (amount * facilitatorFeeBps) / 10000;
        uint256 sellerAmount = amount - feeAmount;

        IERC20Permit3009(teurcToken).receiveWithAuthorization(
            from,
            address(this),
            amount,
            validAfter,
            validBefore,
            nonce,
            v,
            r,
            s
        );

        if (feeAmount > 0) {
            IERC20Permit3009(teurcToken).transfer(owner, feeAmount);
        }
        IERC20Permit3009(teurcToken).transfer(seller, sellerAmount);

        emit AtomicPaymentSettled(from, seller, amount, sellerAmount, feeAmount, nonce);
    }

    function setFeeBps(uint256 _newFeeBps) external onlyOwner {
        if (_newFeeBps > 1000) revert InvalidFeeConfiguration();
        facilitatorFeeBps = _newFeeBps;
    }
}
