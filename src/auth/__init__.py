import hashlib
import os
from database.repositories.user_repository import UserRepository

from logger import logger

auth_message = "APP-TEMPLATE"

def hash_password(password: str) -> str:
    """
    使用SHA256对密码进行加密
    """
    return hashlib.sha256(password.encode('utf-8')).hexdigest()

def auth(username, password):
    """
    验证用户名和密码
    :param username: 用户名
    :param password: 明文密码
    :return: 验证成功返回True，否则返回False
    """
    # 管理员账号从环境变量读取（ADMIN_USERNAME / ADMIN_PASSWORD），未配置则禁用硬编码后门
    admin_username = os.getenv("ADMIN_USERNAME")
    admin_password = os.getenv("ADMIN_PASSWORD")
    if admin_username and admin_password and username == admin_username and password == admin_password:
        return True
    
    # 从数据库查询用户
    user_repo = UserRepository()
    user = user_repo.find_by_username(username)
    
    # 用户不存在
    if not user:
        return False
    
    # 验证密码
    password_hash = hash_password(password)
    return user.password_hash == password_hash

def register_user(username: str, email: str, password: str, **kwargs) -> bool:
    """
    注册新用户
    :param username: 用户名
    :param email: 邮箱
    :param password: 明文密码
    :param kwargs: 其他用户属性
    :return: 注册成功返回True，否则返回False
    """
    try:
        user_repo = UserRepository()
        
        # 检查用户名是否已存在
        existing_user = user_repo.find_by_username(username)
        if existing_user:
            return False
        
        # 创建新用户
        from datetime import datetime
        from database.models.user import User
        
        # 对密码进行SHA256加密
        password_hash = hash_password(password)
        
        # 创建用户对象
        user = User(
            username=username,
            email=email,
            password_hash=password_hash,
            is_active=True,
            created_at=datetime.now(),
            updated_at=datetime.now(),
            **kwargs
        )
        
        # 保存到数据库
        user_id = user_repo.create(user)
        return user_id is not None
    except Exception as e:
        logger.error(f"注册用户失败: {str(e)}")
        return False