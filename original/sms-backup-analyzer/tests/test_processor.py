import unittest
from src.processor import MessageProcessor

class TestMessageProcessor(unittest.TestCase):

    def setUp(self):
        self.processor = MessageProcessor()

    def test_add_message_unique(self):
        message = {
            'address': '1234567890',
            'date': '2023-10-01T12:00:00',
            'body': 'Hello, World!',
            'type': '1'
        }
        self.processor.add_message(message)
        unique_messages = self.processor.get_unique_messages()
        self.assertEqual(len(unique_messages), 1)
        self.assertEqual(unique_messages[0]['body'], 'Hello, World!')

    def test_add_message_duplicate(self):
        message1 = {
            'address': '1234567890',
            'date': '2023-10-01T12:00:00',
            'body': 'Hello, World!',
            'type': '1'
        }
        message2 = {
            'address': '1234567890',
            'date': '2023-10-01T12:00:00',
            'body': 'Hello, World!',
            'type': '1'
        }
        self.processor.add_message(message1)
        self.processor.add_message(message2)
        unique_messages = self.processor.get_unique_messages()
        self.assertEqual(len(unique_messages), 1)

    def test_add_message_different(self):
        message1 = {
            'address': '1234567890',
            'date': '2023-10-01T12:00:00',
            'body': 'Hello, World!',
            'type': '1'
        }
        message2 = {
            'address': '1234567890',
            'date': '2023-10-01T12:01:00',
            'body': 'Hello, Universe!',
            'type': '1'
        }
        self.processor.add_message(message1)
        self.processor.add_message(message2)
        unique_messages = self.processor.get_unique_messages()
        self.assertEqual(len(unique_messages), 2)

if __name__ == '__main__':
    unittest.main()