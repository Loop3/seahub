import django.dispatch

grpmsg_added = django.dispatch.Signal(providing_args=["group_id", "from_email", "message"])
grpmsg_reply_added = django.dispatch.Signal(providing_args=["msg_id", "from_email", "grpmsg_topic", "reply_msg"])

group_join_request = django.dispatch.Signal(providing_args=["staffs", "username", "group", "join_reqeust_msg"])
add_user_to_group = django.dispatch.Signal(providing_args=["group_staff", "group_id", "added_user"])
